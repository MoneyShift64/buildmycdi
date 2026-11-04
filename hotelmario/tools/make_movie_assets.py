#!/usr/bin/env python3
"""Original CD-i animation sectors -> HMV1 PAL8/XOR frames and mono 37800 PCM.
Uses the map's sector-count RLE and CD sector timestamps, not a guessed frame rate.
No gameplay audio assets are changed. Requires Pillow and numpy at conversion only.
"""
import array,json,pathlib,struct,sys,zlib
import numpy as np
from PIL import Image
import make_dc_assets as A
from make_frontend_assets import decode_rl7
W,H=384,280
SIZE=512*256+512*32
# Precompute the existing renderer's twiddled byte destinations once.
def destinations(w,h):
    m=min(w,h);lm=m.bit_length()-1
    return np.array([A.spread(y&(m-1))|(A.spread(x&(m-1))<<1)|
        (((x>>lm) if w>h else (y>>lm))<<(2*lm)) for y in range(h) for x in range(w)])
DEST=[destinations(512,256),destinations(512,32)]
def texture(px):
    result=[]
    for y,rows,h,dest in [(0,256,256,DEST[0]),(256,24,32,DEST[1])]:
        canvas=np.zeros((h,512),dtype=np.uint8);canvas[:rows,:384]=px[y:y+rows]
        out=np.empty(h*512,dtype=np.uint8);out[dest]=canvas.ravel();result.append(out)
    return np.concatenate(result)
class Disc:
    def __init__(self,src,stem):
        self.raw=(src/(stem+'.rtf.raw')).read_bytes()
        self.map=(src/('cdi_'+stem+'.map')).read_bytes()
        self.n,self.at,self.vt,self.dt=struct.unpack_from('>4H',self.map,0x44)
        self.recs=[struct.unpack_from('>I4H',self.map,0x4c+i*12) for i in range(self.n)]
    def sectors(self,rec):
        start=self.recs[rec][0];end=self.recs[rec+1][0] if rec+1<self.n else len(self.raw)//2352
        for i in range(start,end):
            s=self.raw[i*2352:(i+1)*2352]
            yield i,s[18],s[19],s[24:24+2324]
    def sizes(self,rec,table,col,total):
        pos=0x44+table+self.recs[rec][col];out=[]
        while sum(out)<total:
            c=self.map[pos];pos+=1
            if c&128:
                count=257-c;out.extend([self.map[pos]]*count);pos+=1
            else:out.extend(self.map[pos:pos+c+1]);pos+=c+1
            if len(out)>20000 or any(x==0 for x in out):raise ValueError('Invalid map sizes')
        assert sum(out)==total,(rec,total,sum(out))
        return out
    def pictures(self,rec):
        sectors=list(self.sectors(rec));vid=[p for _,sm,_,p in sectors if sm&2]
        palettes=[p[:384] for _,sm,_,p in sectors if sm&8]
        sizes=self.sizes(rec,self.vt,3,len(vid));result=[];at=0
        for i,n in enumerate(sizes):
            raw=b''.join(vid[at:at+n]);at+=n
            if raw.startswith(b'FORM'):
                pos=raw.index(b'IDAT');length=struct.unpack_from('>I',raw,pos+4)[0];raw=raw[pos+8:pos+8+length]
            result.append((np.frombuffer(decode_rl7(raw),dtype=np.uint8).reshape(H,W),palettes[min(i,len(palettes)-1)]))
        return result
    def pcm(self,records):
        hist=[[0,0],[0,0]];result=array.array('h');k0=[0,60,115,98];k1=[0,0,-52,-55]
        for rec in records:
            for _,sm,coding,data in self.sectors(rec):
                if not sm&4:continue
                stereo=bool(coding&1);slow=bool(coding&4)
                assert not coding&16,'8 bit XA not supported'
                for g in range(18):
                    block=data[g*128:(g+1)*128];units=[]
                    for u in range(8):
                        param=block[4+u];shift=param&15;f=(param>>4)&3;ch=u%2 if stereo else 0
                        samples=[]
                        for t in range(28):
                            v=(block[16+4*t+u//2]>>(4*(u%2)))&15
                            if v&8:v-=16
                            v=((v<<12)>>shift)+((hist[ch][0]*k0[f]+hist[ch][1]*k1[f]+32)>>6)
                            v=max(-32768,min(32767,v));hist[ch][1]=hist[ch][0];hist[ch][0]=v;samples.append(v)
                        units.append(samples)
                    for u in range(0,8,2 if stereo else 1):
                        for t in range(28):
                            v=(units[u][t]+units[u+1][t])//2 if stereo else units[u][t]
                            result.extend([v,v] if slow else [v])
        if sys.byteorder!='little':result.byteswap()
        return result.tobytes()
def animation(d,bg,fg,audio,anim):
    # cd_mul_bkg streams both adjoining background-map entries. The next
    # entry is the last background, not a foreground still (build 29 got this
    # wrong and clamped the final camera cut onto the preceding background).
    backgrounds=d.pictures(bg)+d.pictures(fg);secs=list(d.sectors(anim))
    vidcount=sum(bool(sm&2) for _,sm,_,_ in secs);sizes=iter(d.sizes(anim,d.vt,3,vidcount));need=next(sizes);packet=[];index=0
    base=next(i for i,sm,_,_ in d.sectors(audio) if sm&4)
    def compose(front,pal,background):
        back,bpal=background
        return np.where(front!=0,front,back.astype(np.uint16)+128).astype(np.uint8),pal+bpal
    # cd_clut primes the first frame from the animation record itself.
    # It must not display the final background as an initial foreground.
    pal=next(payload[:384] for _,sm,_,payload in secs if sm&8)
    first=b''.join(payload for _,sm,_,payload in secs if sm&2)[:need*2324]
    foreground=np.frombuffer(decode_rl7(first),dtype=np.uint8).reshape(H,W)
    yield 0,*compose(foreground,pal,backgrounds[0])
    for i,sm,_,payload in secs:
        if sm&16:
            index+=1
            if index>=len(backgrounds):raise ValueError('Background trigger exceeds decoded backgrounds')
        if sm&8:pal=payload[:384]
        if sm&2:
            packet.append(payload)
            if len(packet)==need:
                px=np.frombuffer(decode_rl7(b''.join(packet)),dtype=np.uint8).reshape(H,W)
                yield round((i+1-base)*1000/75),*compose(px,pal,backgrounds[index])
                packet=[];need=next(sizes,None)
    assert not packet

def credits(d):
    secs=list(d.sectors(0));pal=next(p[:384] for _,sm,_,p in secs if sm&8)
    back=np.frombuffer(b''.join(p for _,sm,_,p in secs if sm&2)[:W*H],dtype=np.uint8).reshape(H,W)
    secs=list(d.sectors(1));fpal=next(p[:384] for _,sm,_,p in secs if sm&8)
    raw=b''.join(p for _,sm,_,p in secs if sm&2)
    # Original exit_hm scrolls 0xa5c lines, with a two-second initial hold.
    front=np.frombuffer(decode_rl7(raw,W,0xa5c+H),dtype=np.uint8).reshape(-1,W)
    front=np.pad(front,((0,20),(0,0)))
    for frame in range(1377):
        scroll=max(0,frame*2-100);view=front[20+scroll:20+scroll+H]
        yield frame*40,np.where(view!=0,view,back.astype(np.uint16)+128).astype(np.uint8),fpal+pal

def export(out,name,frames,pcm,preview):
    count=0;last=np.zeros(SIZE,dtype=np.uint8);lastpts=0
    path=out/(name+'.hmv')
    with path.open('wb') as f:
        f.write(b'HMV1'+bytes(12))
        for pts,px,pal in frames:
            assert len(pal)==768 and pts>=lastpts
            tx=texture(px);delta=np.bitwise_xor(tx,last).tobytes();last=tx
            blob=zlib.compress(pal+delta,6);f.write(struct.pack('<II',pts,len(blob)));f.write(blob)
            if count in (0,20,100):
                im=Image.fromarray(px);im.putpalette(pal);im.convert('RGB').save(preview/(name+f'-{count}.png'))
            count+=1;lastpts=pts
        duration=max(lastpts+100,len(pcm)*1000//(37800*2))
        f.seek(4);f.write(struct.pack('<III',count,duration,SIZE))
    (out/(name+'.hpa')).write_bytes(b'HPA1'+struct.pack('<I',len(pcm))+zlib.compress(pcm,6))
    record=dict(name=name,frames=count,duration_ms=duration,video_bytes=path.stat().st_size,audio_bytes=len(pcm))
    print(record,flush=True);return record

def main(src,out,preview):
    src,out,preview=map(pathlib.Path,(src,out,preview));out.mkdir(parents=True,exist_ok=True);preview.mkdir(parents=True,exist_ok=True);report=[]
    d=Disc(src,'intro');report.append(export(out,'movie_intro',animation(d,1,2,3,4),d.pcm([3,4]),preview))
    for hotel in [1,2,3,4,5,7,8]:
        d=Disc(src,f'L{hotel}_am')
        for name,indices in [('entry',(0,1,2,3)),('bonus',(11,12,13,14))]:
            report.append(export(out,f'movie_{name}_{hotel}',animation(d,*indices),d.pcm(indices[2:]),preview))
    d=Disc(src,'exit');report.append(export(out,'movie_ending',animation(d,3,4,5,6),d.pcm([5,6]),preview))
    pcm=bytes(37800*2*2)+d.pcm([2]);report.append(export(out,'movie_credits',credits(d),pcm,preview))
    (preview/'manifest.json').write_text(json.dumps(report,indent=2)+'\n')
if __name__=='__main__':main(*sys.argv[1:])
