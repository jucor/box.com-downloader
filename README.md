# Box.com PDF Downloader

Download protected PDF files from Box.com preview pages by capturing rendered page images.

## Installation

Requires Python 3.10+ and [uv](https://github.com/astral-sh/uv).

```sh
git clone https://github.com/lfasmpao/box.com-downloader
cd box.com-downloader
uv sync
```

ChromeDriver is automatically downloaded by `webdriver-manager`.

For OCR support, install `ocrmypdf`:
```sh
brew install ocrmypdf  # macOS
```

## Usage

```sh
uv run python main.py <box.com-url>
```

### Examples

```sh
# Basic download
uv run python main.py https://app.box.com/s/hs5de51wub2htrcl0hxn1wir4zpmf3wj

# Download with grayscale conversion (smaller file)
uv run python main.py --grayscale https://app.box.com/s/...

# Download with OCR (searchable text)
uv run python main.py --ocr https://app.box.com/s/...

# Full pipeline: grayscale + OCR
uv run python main.py --grayscale --ocr https://app.box.com/s/...

# Process existing images (skip download)
uv run python main.py --from-images dl_files/MyDocument --grayscale --ocr
```

### Options

| Option | Description |
|--------|-------------|
| `--grayscale`, `-g` | Convert to grayscale (smaller file) |
| `--ocr` | Add searchable text layer (requires `ocrmypdf`) |
| `--ocr-lang` | OCR language, e.g., `eng`, `fra`, `eng+fra` (default: `eng`) |
| `--from-images`, `-i` | Process existing images instead of downloading |
| `--no-pdf` | Don't create PDF, just download images |
| `--no-keep-images` | Delete images after creating PDF |
| `--max-pages` | Limit number of pages to capture |

## Post-processing

To reprocess previously downloaded images (e.g., add grayscale or OCR):

```sh
uv run python main.py --from-images dl_files/MyDocument --grayscale --ocr
```

### Manual alternative

If you prefer to run tools directly:

```sh
# Convert images to PDF
img2pdf dl_files/page_*.png -o output.pdf

# Convert to grayscale with ImageMagick
magick mogrify -colorspace Gray dl_files/page_*.png

# Add OCR (install: brew install ocrmypdf)
ocrmypdf --optimize 0 --skip-text --jobs 4 input.pdf output_ocr.pdf
```

## License

GNU General Public License v3.0
