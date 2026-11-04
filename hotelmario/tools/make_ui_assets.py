#!/usr/bin/env python3
"""Extract original title art and the shared proportional font. No generated artwork.
Usage: python3 make_ui_assets.py extracted_dir dc/data
Requires Pillow via make_dc_assets; does not require sprite_runner.
"""
import pathlib, struct, sys
import make_dc_assets as A

def main(src, out):
    root = pathlib.Path(__file__).resolve().parents[1]
    src, out = pathlib.Path(src), pathlib.Path(out)
    out.mkdir(parents=True, exist_ok=True)
    records = A.map_records(str(src / 'cdi_intro.map'))
    palette, pixels = A.av_record(str(src / 'intro.rtf.raw'), records[6])
    assert len(pixels) >= 384 * 280
    A.write_tex(str(out / 'menu_title.tex'), pixels[:384 * 280], 384, 280)
    A.write_pal(str(out / 'menu_title.pal'), [tuple(palette[i:i+3]) for i in range(0,384,3)])
    palette, pixels = A.av_record(str(src / 'intro.rtf.raw'), records[5])
    image = (root / 'work/hm_image.bin').read_bytes()
    # Original alphablit table (environ + 4); 49 glyphs A..q. Font stride is
    # 464, height 7; the original alpha2_move doubles pixels in both directions.
    offsets = struct.unpack_from('>50H', image, 0x101888)
    assert offsets[0] == 0 and all(a < b for a,b in zip(offsets, offsets[1:]))
    atlas = bytearray([255]) * (512 * 8)
    for y in range(7):
        for x in range(473):
            value = pixels[y * 464 + x]
            atlas[y * 512 + x] = value if value else 255
    A.write_tex(str(out / 'menu_font.tex'), atlas, 512, 8, fill=255)
    with (out / 'menu_font.frm').open('wb') as f:
        f.write(b'HMFR' + struct.pack('<HH',49,49))
        for a,b in zip(offsets,offsets[1:]): f.write(struct.pack('<4H2h',a,0,b-a,7,0,0))
        f.write(struct.pack('<49H',*range(49)))
    A.write_pal(str(out / 'menu_font.pal'), [tuple(palette[i:i+3]) for i in range(0,384,3)])
    from make_frontend_assets import main as make_frontend
    make_frontend(src, out)
    print('Original title picture, 49-glyph font, metrics and palettes converted.')

if __name__ == '__main__':
    main(*sys.argv[1:])
