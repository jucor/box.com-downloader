#!/usr/bin/env python3
# Box.com PDF Downloader
# Copyright (C) 2018 lfasmpao
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
import glob
import io
import os
import subprocess
import sys

import click
import img2pdf
from PIL import Image

from scraper import Scraper, url_checker


@click.command()
@click.argument('url', required=False)
@click.option('--driver-path', default=None, help='Specify your chrome driver path')
@click.option('--wait-time', default=15, type=int, help='Wait time for selenium to load in seconds')
@click.option('--out', '-o', default=None, help='Output file folder location')
@click.option('--max-pages', default=None, type=int, help='Maximum pages to capture (for testing)')
@click.option('--scroll-pause', default=1.5, type=float, help='Pause between scrolls in seconds')
@click.option('--cdp/--no-cdp', default=True, help='Use CDP network interception (faster)')
@click.option('--pdf/--no-pdf', default=True, help='Concatenate all pages into a single PDF')
@click.option('--keep-images/--no-keep-images', default=True, help='Keep individual images after creating PDF')
@click.option('--grayscale', '-g', is_flag=True, help='Convert images to grayscale before creating PDF')
@click.option('--ocr', is_flag=True, help='Add OCR text layer to PDF (requires ocrmypdf)')
@click.option('--ocr-lang', default='eng', help='OCR language (e.g., eng, fra, eng+fra)')
@click.option('--from-images', '-i', default=None, type=click.Path(exists=True),
              help='Skip download, process existing images from this folder')
@click.version_option(version='2.0', prog_name='Box.com PDF Downloader')
def main(url, driver_path, wait_time, out, max_pages, scroll_pause, cdp, pdf, keep_images,
         grayscale, ocr, ocr_lang, from_images):
    """Download PDF previews from Box.com shared links.

    URL: The box.com shared URL to download from (optional if using --from-images)
    """
    style = "=+" * 20

    click.echo(style)
    click.secho("Box.com PDF Downloader", fg='cyan', bold=True)

    # Default output location
    if out is None:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dl_files")

    dl_name = None
    output_dir = None
    num_images = 0

    # Mode 1: Process existing images from folder
    if from_images:
        output_dir = from_images
        dl_name = os.path.basename(output_dir.rstrip('/'))
        image_files = sorted(glob.glob(os.path.join(output_dir, "page_*.png")))
        image_files += sorted(glob.glob(os.path.join(output_dir, "page_*.jpg")))
        image_files = sorted(image_files)
        num_images = len(image_files)

        if num_images == 0:
            raise click.ClickException(f"No images found in {output_dir}")

        click.echo(f"Processing {num_images} existing images from: {output_dir}")

    # Mode 2: Download from URL
    else:
        if not url:
            raise click.UsageError("URL is required (or use --from-images to process existing images)")

        if not url_checker(url):
            raise click.BadParameter('URL must be a valid box.com URL (http:// or https://)')

        box_object = None
        try:
            box_object = Scraper(url, driver_path, wait_time)
            box_object.load_url()
            dl_name = box_object.get_download_title()

            click.echo(style)
            click.echo(f"Title: {click.style(dl_name, fg='green')}")
            click.echo(f"URL: {url}")

            # Create output directory for images
            output_dir = os.path.join(out, dl_name)
            os.makedirs(output_dir, exist_ok=True)

            click.echo(style)
            if cdp:
                click.echo("Capturing preview images via CDP network interception...")
                num_images = box_object.capture_preview_images_cdp(output_dir, scroll_pause=scroll_pause, max_pages=max_pages)
            else:
                click.echo("Capturing preview images by scrolling...")
                num_images = box_object.capture_preview_images(output_dir, scroll_pause=scroll_pause, max_pages=max_pages)

            click.echo(style)
            click.secho(f"✓ Captured {num_images} images to: {output_dir}", fg='green')
        finally:
            if box_object:
                box_object.clean()

    # Create PDF if requested
    if pdf and num_images > 0 and dl_name and output_dir:
        pdf_path = os.path.join(out, f"{dl_name}.pdf")
        click.echo(f"Creating PDF: {pdf_path}")

        # Get all images sorted by name
        image_files = sorted(glob.glob(os.path.join(output_dir, "page_*.png")))
        image_files += sorted(glob.glob(os.path.join(output_dir, "page_*.jpg")))
        image_files = sorted(image_files)

        if image_files:
            # Convert to grayscale if requested
            if grayscale:
                click.echo("Converting to grayscale...")
                image_data = []
                for img_path in image_files:
                    img = Image.open(img_path).convert('L')
                    buf = io.BytesIO()
                    img.save(buf, format='PNG')
                    image_data.append(buf.getvalue())
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(image_data))
            else:
                # img2pdf embeds images losslessly without re-encoding
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(image_files))

            click.secho(f"✓ Created PDF: {pdf_path}", fg='green')

            # Run OCR if requested
            if ocr:
                ocr_pdf_path = os.path.join(out, f"{dl_name}_ocr.pdf")
                click.echo(f"Running OCR (this may take a while)...")

                # Check if ocrmypdf is installed
                try:
                    subprocess.run(["ocrmypdf", "--version"], capture_output=True, check=True)
                except (subprocess.CalledProcessError, FileNotFoundError):
                    click.secho("Error: ocrmypdf is not installed. Install with: brew install ocrmypdf", fg='red')
                    sys.exit(1)

                cmd = [
                    "ocrmypdf",
                    "--language", ocr_lang,
                    "--optimize", "0",
                    "--skip-text",
                    "--jobs", "4",
                    pdf_path,
                    ocr_pdf_path
                ]

                result = subprocess.run(cmd)
                if result.returncode == 0:
                    click.secho(f"✓ Created OCR PDF: {ocr_pdf_path}", fg='green')
                else:
                    click.secho(f"OCR failed with exit code {result.returncode}", fg='red')

            # Remove images if not keeping them
            if not keep_images:
                for img_file in image_files:
                    os.remove(img_file)
                os.rmdir(output_dir)
                click.echo("Cleaned up individual images")


if __name__ == "__main__":
    main()
