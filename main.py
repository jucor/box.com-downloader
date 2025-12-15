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
import os

import click
import img2pdf

from scraper import Scraper, url_checker


@click.command()
@click.argument('url')
@click.option('--driver-path', default=None, help='Specify your chrome driver path')
@click.option('--wait-time', default=15, type=int, help='Wait time for selenium to load in seconds')
@click.option('--out', '-o', default=None, help='Output file folder location')
@click.option('--max-pages', default=None, type=int, help='Maximum pages to capture (for testing)')
@click.option('--scroll-pause', default=1.5, type=float, help='Pause between scrolls in seconds')
@click.option('--cdp/--no-cdp', default=True, help='Use CDP network interception (faster)')
@click.option('--pdf/--no-pdf', default=True, help='Concatenate all pages into a single PDF')
@click.option('--keep-images/--no-keep-images', default=True, help='Keep individual images after creating PDF')
@click.version_option(version='2.0', prog_name='Box.com PDF Downloader')
def main(url, driver_path, wait_time, out, max_pages, scroll_pause, cdp, pdf, keep_images):
    """Download PDF previews from Box.com shared links.

    URL: The box.com shared URL to download from
    """
    style = "=+" * 20

    if not url_checker(url):
        raise click.BadParameter('URL must be a valid box.com URL (http:// or https://)')

    click.echo(style)
    click.secho("Box.com PDF Downloader", fg='cyan', bold=True)

    # Default output location
    if out is None:
        out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dl_files")

    box_object = None
    dl_name = None
    output_dir = None
    num_images = 0

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
        image_files = sorted(image_files)  # Re-sort in case of mixed extensions

        if image_files:
            # img2pdf embeds images losslessly without re-encoding
            # Use layout_fun to set proper page size based on image dimensions
            with open(pdf_path, "wb") as f:
                f.write(img2pdf.convert(
                    image_files,
                    # Use 72 DPI (default) - images are embedded at their native resolution
                    # No resampling or compression is applied
                ))
            click.secho(f"✓ Created PDF: {pdf_path}", fg='green')

            # Remove images if not keeping them
            if not keep_images:
                for img_file in image_files:
                    os.remove(img_file)
                os.rmdir(output_dir)
                click.echo("Cleaned up individual images")


if __name__ == "__main__":
    main()
