#!/usr/bin/env python3
"""Original intro-map DYUV pictures and selection masks -> Dreamcast PAL8 assets.
DYUV arithmetic checked against MAME's BSD-3-Clause mcd212.cpp implementation:
https://github.com/mamedev/mame/blob/master/src/mame/philips/mcd212.cpp
The pixels come from the user's original disc; no replacement artwork.
"""
import pathlib, sys
from PIL import Image
import make_dc_assets as A
DELTA = (0,1,4,9,16,27,44,79,128,177,212,229,240,247,252,255)
def decode_dyuv(data, width=384, height=280):
    if len(data) < width * height: raise ValueError('Truncated DYUV picture')
    result = []
    def rgb(y,u,v):
        return tuple(max(0,min(255,c)) & 254 for c in
            (y+((351*(v-128))>>8), y+((-86*(u-128)-179*(v-128))>>8),
             y+((444*(u-128))>>8)))
    for row in range(height):
        y=u=v=128
        line=data[row*width:(row+1)*width]; pairs=[]
        for a,b in zip(line[::2],line[1::2]):
            u=(u+DELTA[a>>4])&255;v=(v+DELTA[b>>4])&255
            first=(y+DELTA[a&15])&255;y=(first+DELTA[b&15])&255
            pairs.append((first,y,u,v))
        for i,(a,b,u,v) in enumerate(pairs):
            un,vn=pairs[min(i+1,len(pairs)-1)][2:]
            result.extend((rgb(a,u,v),rgb(b,(u+un)//2,(v+vn)//2)))
    image=Image.new('RGB',(width,height));image.putdata(result);return image

def decode_rl7(data, width=384, height=280):
    pixels=bytearray();at=0
    for _ in range(height):
        row=bytearray()
        while len(row)<width:
            value=data[at];at+=1
            if value & 128:
                count=data[at];at+=1
                if not count: count=width-len(row)
                row.extend([value & 127]*count)
            else:row.append(value)
        if len(row)!=width: raise ValueError('Invalid RL7 row')
        pixels.extend(row)
    return pixels

def main(src,out):
    src,out=pathlib.Path(src),pathlib.Path(out);out.mkdir(parents=True,exist_ok=True)
    records=A.map_records(str(src/'cdi_intro.map'))
    for hotel in [1,2,3,4,5,7,8]:
        _,raw=A.av_record(str(src/'intro.rtf.raw'),records[hotel+7])
        image=decode_dyuv(raw)
        # PAL8 keeps the existing renderer/audio path; map only, never gameplay.
        indexed=image.quantize(colors=128,method=Image.Quantize.MEDIANCUT,dither=Image.Dither.NONE)
        A.write_tex(str(out/f'map_{hotel}.tex'),indexed.tobytes(),384,280)
        pal=indexed.getpalette()[:384]
        A.write_pal(str(out/f'map_{hotel}.pal'),[tuple(pal[i:i+3]) for i in range(0,384,3)])
    for hotel in [1,2,3,4,5,7,8]:
        av=src/f'L{hotel}_av.rtf.raw'
        start=A.map_records(str(src/f'cdi_L{hotel}_av.map'))[0]
        pal,raw=A.av_record(str(av),start)
        pixels=decode_rl7(raw)
        A.write_tex(str(out/f'continue_{hotel}.tex'),pixels,384,280)
        A.write_pal(str(out/f'continue_{hotel}.pal'),[tuple(pal[i:i+3]) for i in range(0,384,3)])
    _,mask=A.av_record(str(src/'intro.rtf.raw'),records[16])
    assert set(mask[:384*280]) <= {0,1,2,3,4,5,7,8}
    A.write_tex(str(out/'map_mask.tex'),mask[:384*280],384,280)
    print('Exported seven original DYUV hotel maps and the original hotel selection mask.')
if __name__=='__main__':main(*sys.argv[1:])
