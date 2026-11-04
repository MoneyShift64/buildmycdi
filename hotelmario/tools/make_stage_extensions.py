#!/usr/bin/env python3
"""Export upper rooms and the original per-stage puzzle data; no emulator needed."""
import pathlib, struct, sys, json
import make_dc_assets as A
import dump_sprites as D

def info(si):
    return (b'HMSI' + struct.pack('<35H', *struct.unpack_from('>35H',si,0x12)) +
            struct.pack('<I',struct.unpack_from('>I',si,8)[0]) +
            struct.pack('<30h',*struct.unpack_from('>30h',si,0x58)) +
            struct.pack('<I2h',struct.unpack_from('>I',si,4)[0],*struct.unpack_from('>2h',si,0xe)))

def main(src, out):
    src,out=pathlib.Path(src),pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    inventory=[]
    for h in [0,1,2,3,4,5,7,8]:
        dat=src/f'L{h}_dat.rtf.raw';av=src/f'L{h}_av.rtf.raw'
        recs=A.map_records(str(src/f'cdi_L{h}_av.map'))
        for st in range(len(recs)//8):
            tag=f'{h}_{st+1:02d}'
            for upper in ([False,True] if h>=7 else [False]):
                suffix='_u' if upper else ''
                module,rest=A.stage_record(str(dat),st,upper)
                assert len(rest)>=0x3be8
                si=rest[0x40:0xd4];names=[n for _,n in D.entries(module)]
                missing=set(names)-set(A.PORTED_OBJECTS)
                if missing:raise ValueError((tag,upper,'unported',missing))
                tail=rest[0x3be8:]
                (out/f'stage_{tag}{suffix}.ext').write_bytes(b'HMEX'+struct.pack('<HHI',1,0,len(tail))+tail)
                if upper:
                    pal,_=A.av_record(str(av),recs[st*8+3])
                    _,px=A.av_record(str(av),recs[st*8+5])
                    assert len(px)>=384*280,(tag,len(px))
                    A.write_tex(str(out/f'stage_{tag}_u.tex'),px[:384*280],384,280)
                    A.write_pal(str(out/f'stage_{tag}_u.pal'),[tuple(pal[i:i+3]) for i in range(0,384,3)])
                    (out/f'stage_{tag}_u.inf').write_bytes(info(si))
                    A.write_room(str(out/f'room_{tag}_u.room'),str(dat),st,True)
                inventory.append(dict(tag=tag,upper=upper,module_size=len(module),module_name=next(D.modules_in(module))[0],entries=names,actors=struct.unpack_from('>h',rest,0xd4)[0],extra_bytes=len(tail)))
    (out/'stage_inventory.json').write_text(json.dumps(inventory,indent=2)+'\n')
    print('Exported',len(inventory),'rooms, including',sum(x['upper'] for x in inventory),'upper rooms; handler inventory recorded.')

if __name__=='__main__':main(*sys.argv[1:])
