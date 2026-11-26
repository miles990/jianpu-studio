# Jianpu Studio app icon

`icon-1024.png` is the 1024 × 1024 RGBA master. The rounded paper plate has a transparent 10% inset. `icon-256.png` is the same art at 256 × 256. `icon.icns` contains the five macOS sizes (16, 32, 128, 256, and 512 px) at 1× and 2×. `icon.ico` uses a 2% inset and contains 16, 24, 32, 48, 64, 128, and 256 px frames.

## Image generation

The built-in OpenAI ImageGen tool generated the artwork with transparent background enabled.

> Generate one production desktop app icon for Jianpu Studio, a numbered musical notation editor. Square 1024×1024 RGBA canvas with true transparent exterior. Center one flat, front-on, warm paper #F6F2E8 rounded-square plate, 80% canvas width and height, the outer 10% inset on each side, with smooth macOS-style corners. Within it place one large, bold black-ink #1A1D1C geometric sans-serif numeral “5”, optically centered, one clearly detached round octave dot directly above it and exactly one short horizontal eighth-note duration bar centered directly beneath the digit, in the same ink. The mark should be wide and legible when viewed at 16×16 pixels. Add one bamboo-green #0D7560 slender rising swept curve, from lower left toward upper right like a single erhu bow stroke, with taper at both tips and a visible central width about 70% of the digit stroke. Route the arc around the lower left and upward to the right, leaving the numeral's counters entirely clear and maintaining a clean paper gap between arc and ink marks. Keep the central mark and arc large enough to distinguish when downscaled. Only these flat three colors, plus exterior transparency and ordinary antialiasing. No gradients, outlines around the plate, paper texture, lighting, shadows, letters, words, staff lines, physical instrument, mockup, decoration, or additional marks. Return one icon, no presentation board.

## Reproduce the exports

From the repository root, save the RGBA ImageGen output as `generated-icon.png` in a scratch directory. The commands use Pillow from the repository's virtual environment and the macOS `sips` and `iconutil` tools. Set `generated_icon` to the actual source path.

```sh
generated_icon=/path/to/generated-icon.png
export_dir=packaging/icon

.venv/bin/python - "$generated_icon" "$export_dir" <<'PY'
from pathlib import Path
from sys import argv
from PIL import Image

source, out = map(Path, argv[1:])
original = Image.open(source).convert("RGBA")
colors = [246, 242, 232, 26, 29, 28, 13, 117, 96]
palette = Image.new("P", (1, 1))
palette.putpalette(colors + colors[-3:] * 253)
flat = original.convert("RGB").quantize(
    palette=palette, dither=Image.Dither.NONE
).convert("RGBA")
# Clean stray exterior pixels and keep the antialiased plate edge.
alpha = original.getchannel("A").point(
    lambda value: max(0, min(255, round((value - 76) * 255 / 168)))
)
flat.putalpha(alpha)
lanczos = Image.Resampling.LANCZOS
plate = flat.crop(alpha.getbbox()).resize((820, 820), lanczos)
master = Image.new("RGBA", (1024, 1024))
master.paste(plate, (102, 102))
master.save(out / "icon-1024.png", optimize=True)

windows = Image.new("RGBA", (256, 256))
windows.paste(plate.resize((246, 246), lanczos), (5, 5))
windows.save(out / "icon.ico", sizes=[(n, n) for n in (16, 24, 32, 48, 64, 128, 256)])
PY

sips -z 256 256 "$export_dir/icon-1024.png" --out "$export_dir/icon-256.png"

icon_tmp=$(mktemp -d)
iconset="$icon_tmp/Jianpu.iconset"
mkdir "$iconset"
for size in 16 32 128 256 512; do
  edge_2x=$((size * 2))
  sips -z "$size" "$size" "$export_dir/icon-1024.png" \
    --out "$iconset/icon_${size}x${size}.png"
  sips -z "$edge_2x" "$edge_2x" "$export_dir/icon-1024.png" \
    --out "$iconset/icon_${size}x${size}@2x.png"
done
iconutil -c icns "$iconset" -o "$export_dir/icon.icns"
```

The local `iconutil -c icns` returned `Invalid Iconset` for the ten valid PNGs. The delivered file uses the following direct packing step. `iconutil -c iconset` extracted all ten frames from it successfully.

```sh
.venv/bin/python - "$iconset" "$export_dir/icon.icns" <<'PY'
from pathlib import Path
from struct import pack
from sys import argv

iconset, out = map(Path, argv[1:])
frames = {
    "icp4": "16x16", "ic11": "16x16@2x",
    "icp5": "32x32", "ic12": "32x32@2x",
    "ic07": "128x128", "ic13": "128x128@2x",
    "ic08": "256x256", "ic14": "256x256@2x",
    "ic09": "512x512", "ic10": "512x512@2x",
}
chunks = []
for kind, suffix in frames.items():
    png = (iconset / f"icon_{suffix}.png").read_bytes()
    chunks.append(pack(">4sI", kind.encode("ascii"), len(png) + 8) + png)
body = b"".join(chunks)
out.write_bytes(pack(">4sI", b"icns", len(body) + 8) + body)
PY

iconutil -c iconset "$export_dir/icon.icns" -o "$icon_tmp/Verified.iconset"
```

## UI icons and previews

The UI icons in `src/jianpu_studio/assets/icons/` were drawn as SVG geometry on a 24 × 24 grid. Sharp rasterized each SVG at 16, 24, and 48 px with the theme foreground supplied as `color`. Pillow assembled the light and dark contact sheets in `work/icons/` and the app preview from the 16 and 32 px frames in the finished ICNS and ICO.

## License

The app artwork and hand-drawn UI SVGs are original works created for Jianpu Studio and distributed under the project's MIT license.
