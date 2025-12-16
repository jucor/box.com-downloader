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

import atexit
import base64
import glob
import io
import os
import re
import signal
import subprocess
import sys
import time

import click
import img2pdf
from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.action_chains import ActionChains
from tqdm import tqdm
from webdriver_manager.chrome import ChromeDriverManager


def url_checker(url):
    """Check if URL is a valid box.com URL."""
    url_check_regex = re.compile(
        r'^(?:http|ftp)s?://'
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'
        r'localhost|'
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'
        r'(?::\d+)?'
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)
    if re.match(url_check_regex, url) is not None:
        return "box.com" in url
    return False


class Scraper:
    def __init__(self, url, driver_location=None, wait_time=None):
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--window-size=2560,4000")
        chrome_options.add_argument("--force-device-scale-factor=2")
        chrome_options.add_argument("--disable-blink-features=AutomationControlled")
        chrome_options.add_experimental_option("excludeSwitches", ["enable-automation"])
        chrome_options.add_experimental_option('useAutomationExtension', False)
        chrome_options.set_capability('goog:loggingPrefs', {'performance': 'ALL'})

        self.wait_load_time = wait_time
        self.url = url
        if driver_location:
            service = Service(executable_path=driver_location)
        else:
            service = Service(ChromeDriverManager().install())
        self.driver_obj = webdriver.Chrome(service=service, options=chrome_options)

        atexit.register(self._cleanup)
        self._original_sigint = signal.signal(signal.SIGINT, self._signal_handler)
        self._original_sigterm = signal.signal(signal.SIGTERM, self._signal_handler)

    def _cleanup(self):
        try:
            if hasattr(self, 'driver_obj') and self.driver_obj:
                self.driver_obj.quit()
                self.driver_obj = None
        except Exception:
            pass
        try:
            subprocess.run(['pkill', '-f', 'chromedriver'], capture_output=True, timeout=5)
        except Exception:
            pass

    def _signal_handler(self, signum, frame):
        print(f"\nReceived signal {signum}, cleaning up...")
        self._cleanup()
        sys.exit(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.clean()
        return False

    def load_url(self):
        driver = self.driver_obj
        driver.get(self.url)

        max_wait = self.wait_load_time
        poll_interval = 0.5
        waited = 0

        print(f"Waiting for preview to load (max {max_wait}s)...")

        while waited < max_wait:
            ready = driver.execute_script("""
                var imgs = document.querySelectorAll('img[src^="data:"]');
                for (var i = 0; i < imgs.length; i++) {
                    if (imgs[i].src.length > 1000) return true;
                }
                var canvases = document.querySelectorAll('canvas');
                for (var i = 0; i < canvases.length; i++) {
                    var rect = canvases[i].getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 100) return true;
                }
                var containers = document.querySelectorAll('[class*="bp-doc"], [class*="PreviewContent"]');
                for (var i = 0; i < containers.length; i++) {
                    if (containers[i].scrollHeight > 1000) return true;
                }
                return false;
            """)

            if ready:
                print(f"Preview loaded in {waited:.1f}s")
                time.sleep(0.5)
                return

            time.sleep(poll_interval)
            waited += poll_interval

        print(f"Timeout after {max_wait}s, proceeding anyway...")

    def get_download_title(self):
        title = str(self.driver_obj.title).split("|")[0][:-1]
        return str(title.split(".")[:-1][0])

    def capture_preview_images_cdp(self, output_dir, scroll_pause=0.5, max_pages=None):
        driver = self.driver_obj
        os.makedirs(output_dir, exist_ok=True)

        print(f"Starting capture... (max: {max_pages or 'unlimited'})")

        # Trigger hover to show toolbar
        try:
            preview = driver.find_element('css selector', '.bp-doc, [class*="bp-doc"], .bp-content')
            ActionChains(driver).move_to_element(preview).perform()
            for _ in range(20):
                if driver.execute_script("return document.querySelector('.bp-PageControls') !== null;"):
                    break
                time.sleep(0.1)
        except Exception:
            pass

        # Get page count
        page_info = driver.execute_script("""
            var result = {total: null, estimated: null};
            var pageControls = document.querySelector('.bp-PageControls, .bp-PageControlsForm, [class*="PageControls"]');
            if (pageControls) {
                var text = pageControls.textContent || '';
                var match = text.match(/(\\d+)\\s*\\/\\s*(\\d+)/);
                if (match) result.total = parseInt(match[2]);
            }
            if (!result.total) {
                var text = document.body.innerText;
                var match = text.match(/of\\s+(\\d+)/i) || text.match(/(\\d+)\\s+pages?/i);
                if (match) result.total = parseInt(match[1]);
            }
            return result;
        """)

        total_pages = page_info.get('total') or page_info.get('estimated')
        if total_pages:
            print(f"Detected {total_pages} pages")

        pbar_total = max_pages if max_pages else (total_pages or 100)
        pbar = tqdm(total=pbar_total, unit="pages", desc="Capturing", dynamic_ncols=True)

        page_count = 0
        target_pages = max_pages or total_pages or 500
        time.sleep(0.5)

        for target_page in range(1, target_pages + 1):
            if max_pages and page_count >= max_pages:
                break

            if target_page % 20 == 1:
                try:
                    preview = driver.find_element('css selector', '.bp-doc, .bp-content')
                    ActionChains(driver).move_to_element(preview).perform()
                except Exception:
                    pass

            img_data = driver.execute_async_script("""
                var callback = arguments[arguments.length - 1];
                var startTime = Date.now();
                var maxWait = 2000;
                var minWidth = 800;

                function tryCapture() {
                    var viewportCenter = window.innerHeight / 2;
                    var bestElement = null;
                    var bestDistance = Infinity;
                    var bestType = null;

                    var canvases = document.querySelectorAll('canvas');
                    for (var i = 0; i < canvases.length; i++) {
                        var c = canvases[i];
                        var rect = c.getBoundingClientRect();
                        if (c.width >= minWidth && rect.height > 100) {
                            var center = rect.top + rect.height / 2;
                            var distance = Math.abs(center - viewportCenter);
                            if (distance < bestDistance) {
                                bestDistance = distance;
                                bestElement = c;
                                bestType = 'canvas';
                            }
                        }
                    }

                    var imgs = document.querySelectorAll('img');
                    for (var i = 0; i < imgs.length; i++) {
                        var img = imgs[i];
                        var rect = img.getBoundingClientRect();
                        if (img.naturalWidth >= minWidth && rect.height > 100 &&
                            (img.src.startsWith('blob:') || img.src.startsWith('data:'))) {
                            var center = rect.top + rect.height / 2;
                            var distance = Math.abs(center - viewportCenter);
                            if (distance < bestDistance) {
                                bestDistance = distance;
                                bestElement = img;
                                bestType = 'img';
                            }
                        }
                    }

                    if (!bestElement) {
                        if (Date.now() - startTime < maxWait) {
                            setTimeout(tryCapture, 100);
                        } else {
                            callback(null);
                        }
                        return;
                    }

                    try {
                        var dataUrl, width, height;
                        if (bestType === 'canvas') {
                            dataUrl = bestElement.toDataURL('image/png');
                            width = bestElement.width;
                            height = bestElement.height;
                        } else {
                            var canvas = document.createElement('canvas');
                            canvas.width = bestElement.naturalWidth;
                            canvas.height = bestElement.naturalHeight;
                            var ctx = canvas.getContext('2d');
                            ctx.drawImage(bestElement, 0, 0);
                            dataUrl = canvas.toDataURL('image/png');
                            width = canvas.width;
                            height = canvas.height;
                        }
                        if (dataUrl && dataUrl.length > 1000) {
                            callback({ dataUrl: dataUrl, width: width, height: height });
                        } else {
                            callback(null);
                        }
                    } catch(e) {
                        callback(null);
                    }
                }
                tryCapture();
            """)

            if img_data:
                page_count += 1
                try:
                    data_url = img_data['dataUrl']
                    header, data = data_url.split(',', 1)
                    ext = 'png' if 'png' in header else 'jpg'
                    filename = os.path.join(output_dir, f"page_{page_count:04d}.{ext}")
                    with open(filename, 'wb') as f:
                        f.write(base64.b64decode(data))
                    pbar.update(1)
                except Exception as e:
                    pbar.write(f"Error saving page {page_count}: {e}")
                    page_count -= 1
            else:
                pbar.write(f"Warning: Could not capture page {target_page}")

            next_clicked = driver.execute_script("""
                var nextBtn = document.querySelector('[data-testid="bp-PageControls-next"]');
                if (nextBtn && !nextBtn.disabled) {
                    nextBtn.click();
                    return true;
                }
                return false;
            """)

            if not next_clicked:
                break

            time.sleep(scroll_pause * 0.2)

        pbar.close()
        print(f"Captured {page_count} pages")
        return page_count

    def clean(self):
        self._cleanup()
        if hasattr(self, '_original_sigint'):
            signal.signal(signal.SIGINT, self._original_sigint)
        if hasattr(self, '_original_sigterm'):
            signal.signal(signal.SIGTERM, self._original_sigterm)


@click.command()
@click.argument('url', required=False)
@click.option('--driver-path', default=None, help='Specify your chrome driver path')
@click.option('--wait-time', default=15, type=int, help='Wait time for selenium to load in seconds')
@click.option('--out', '-o', default=None, help='Output file folder location')
@click.option('--max-pages', default=None, type=int, help='Maximum pages to capture (for testing)')
@click.option('--scroll-pause', default=1.5, type=float, help='Pause between scrolls in seconds')
@click.option('--pdf/--no-pdf', default=True, help='Concatenate all pages into a single PDF')
@click.option('--keep-images/--no-keep-images', default=True, help='Keep individual images after creating PDF')
@click.option('--grayscale', '-g', is_flag=True, help='Convert images to grayscale before creating PDF')
@click.option('--ocr', is_flag=True, help='Add OCR text layer to PDF (requires ocrmypdf)')
@click.option('--ocr-lang', default='eng', help='OCR language (e.g., eng, fra, eng+fra)')
@click.option('--from-images', '-i', default=None, type=click.Path(exists=True),
              help='Skip download, process existing images from this folder')
@click.version_option(version='2.0', prog_name='Box.com PDF Downloader')
def main(url, driver_path, wait_time, out, max_pages, scroll_pause, pdf, keep_images,
         grayscale, ocr, ocr_lang, from_images):
    """Download PDF previews from Box.com shared links.

    URL: The box.com shared URL to download from (optional if using --from-images)
    """
    style = "=+" * 20

    click.echo(style)
    click.secho("Box.com PDF Downloader", fg='cyan', bold=True)

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

            output_dir = os.path.join(out, dl_name)
            os.makedirs(output_dir, exist_ok=True)

            click.echo(style)
            num_images = box_object.capture_preview_images_cdp(output_dir, scroll_pause=scroll_pause, max_pages=max_pages)

            click.echo(style)
            click.secho(f"✓ Captured {num_images} images to: {output_dir}", fg='green')
        finally:
            if box_object:
                box_object.clean()

    # Create PDF if requested
    if pdf and num_images > 0 and dl_name and output_dir:
        pdf_path = os.path.join(out, f"{dl_name}.pdf")
        click.echo(f"Creating PDF: {pdf_path}")

        image_files = sorted(glob.glob(os.path.join(output_dir, "page_*.png")))
        image_files += sorted(glob.glob(os.path.join(output_dir, "page_*.jpg")))
        image_files = sorted(image_files)

        if image_files:
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
                with open(pdf_path, "wb") as f:
                    f.write(img2pdf.convert(image_files))

            click.secho(f"✓ Created PDF: {pdf_path}", fg='green')

            if ocr:
                ocr_pdf_path = os.path.join(out, f"{dl_name}_ocr.pdf")
                click.echo("Running OCR (this may take a while)...")

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

            if not keep_images:
                for img_file in image_files:
                    os.remove(img_file)
                os.rmdir(output_dir)
                click.echo("Cleaned up individual images")


if __name__ == "__main__":
    main()
