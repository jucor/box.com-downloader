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
import base64
import os
import re
import sys
import time

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from tqdm import tqdm
from webdriver_manager.chrome import ChromeDriverManager


def url_checker(url):
    """
       This checks the url format
       :param url Unified Resource Locator
       :rtype: bool
       :return boolean
    """
    url_check_regex = re.compile(
        r'^(?:http|ftp)s?://'  # http:// or https://
        r'(?:(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,6}\.?|[A-Z0-9-]{2,}\.?)|'  # domain...
        r'localhost|'  # localhost
        r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})'  # ...or ip
        r'(?::\d+)?'  # port
        r'(?:/?|[/?]\S+)$', re.IGNORECASE)  # url check regex
    if re.match(url_check_regex, url) is not None:
        return "box.com" in url  # really?


class Scraper:
    def __init__(self, url, driver_location=None, wait_time=None):
        """
        This starts a selenium session
        :param url box.com url
        :type url string
        :param driver_location chrome driver path
        :type driver_location string
        """

        chrome_options = Options()
        chrome_options.add_argument("--headless")
        chrome_options.add_argument("--window-size=1280x800")

        self.wait_load_time = wait_time
        self.driver_location = driver_location
        self.url = url
        if driver_location:
            service = Service(executable_path=driver_location)
        else:
            service = Service(ChromeDriverManager().install())
        self.driver_obj = webdriver.Chrome(service=service, options=chrome_options)

    def load_url(self):
        """
        This will load the url on the selenium driver.
        Waits for the preview to actually load instead of a fixed time.
        """
        driver = self.driver_obj
        url = self.url
        driver.get(url)  # load selenium

        # Smart wait: poll for preview content instead of fixed sleep
        max_wait = self.wait_load_time
        poll_interval = 0.5
        waited = 0

        print(f"Waiting for preview to load (max {max_wait}s)...")

        while waited < max_wait:
            # Check if preview content has loaded
            ready = driver.execute_script("""
                // Check for data: images (PDF pages rendered)
                var imgs = document.querySelectorAll('img[src^="data:"]');
                for (var i = 0; i < imgs.length; i++) {
                    if (imgs[i].src.length > 1000) return true;
                }

                // Check for canvas elements (alternative rendering)
                var canvases = document.querySelectorAll('canvas');
                for (var i = 0; i < canvases.length; i++) {
                    var rect = canvases[i].getBoundingClientRect();
                    if (rect.width > 100 && rect.height > 100) return true;
                }

                // Check for Box.com preview container with content
                var containers = document.querySelectorAll('[class*="bp-doc"], [class*="PreviewContent"], [class*="preview-content"]');
                for (var i = 0; i < containers.length; i++) {
                    if (containers[i].scrollHeight > 1000) return true;
                }

                return false;
            """)

            if ready:
                print(f"Preview loaded in {waited:.1f}s")
                # Give it a tiny bit more time to fully render
                time.sleep(0.5)
                return

            time.sleep(poll_interval)
            waited += poll_interval

        print(f"Timeout after {max_wait}s, proceeding anyway...")

    def get_download_title(self):
        """
        This parses box.com title
        :rtype: string
        :return download title
        """
        driver = self.driver_obj
        title = str(driver.title).split("|")[0][:-1]
        return str(title.split(".")[:-1][0])  # split and remove extra white space

    def get_download_url(self):
        """
        This parses box.com url into PDF downloadable file
        :rtype string
        :returns box.com download_url else return None
        """
        driver = self.driver_obj  # get driver
        # Load network requests - get only the name property to avoid serialization issues
        network_requests = driver.execute_script(
            "return window.performance.getEntries().map(function(e) { return e.name; });"
        )
        download_url = None  # default or error
        for name in network_requests:
            # this will scrap the pdf file in the word
            if ("internal_files" in name) and ("pdf" in name):  # check for a pdf file
                download_url = name
        return download_url

    def capture_preview_images(self, output_dir, scroll_pause=1.5, max_pages=None):
        """
        Scrolls through the PDF preview and captures all data:// images.
        Saves them as numbered image files.
        :param output_dir: Directory to save the images
        :param scroll_pause: Time to wait after each scroll for images to load
        :param max_pages: Maximum number of pages to capture (None for unlimited)
        :return: Number of images captured
        """
        driver = self.driver_obj
        os.makedirs(output_dir, exist_ok=True)

        page_images = []  # Track captured pages
        last_image_count = -1
        no_new_images_count = 0

        print(f"Starting to capture preview images... (max: {max_pages or 'unlimited'})")

        try:
            # Wait for initial load
            time.sleep(3)

            # Try to find the Box.com preview iframe or container
            # First, let's see what we're working with
            container_info = driver.execute_script("""
                // Find scrollable containers
                var scrollables = [];
                var all = document.querySelectorAll('*');
                for (var i = 0; i < all.length; i++) {
                    var el = all[i];
                    var style = window.getComputedStyle(el);
                    if (style.overflowY === 'scroll' || style.overflowY === 'auto') {
                        if (el.scrollHeight > el.clientHeight) {
                            scrollables.push({
                                tag: el.tagName,
                                class: el.className,
                                id: el.id,
                                scrollHeight: el.scrollHeight,
                                clientHeight: el.clientHeight
                            });
                        }
                    }
                }
                return scrollables;
            """)
            print(f"Found {len(container_info)} scrollable containers")

            # Estimate total pages from scroll height
            estimated_pages = None
            for c in container_info[:5]:
                print(f"  - {c['tag']}.{c['class'][:50] if c['class'] else ''} (scroll: {c['scrollHeight']}, client: {c['clientHeight']})")
                if c['scrollHeight'] > 10000:  # Likely the main container
                    # Estimate ~1000px per page
                    estimated_pages = c['scrollHeight'] // 1000

            # Get total number of pages if available from text
            total_pages = driver.execute_script("""
                // Look for page count indicators
                var text = document.body.innerText;
                var match = text.match(/of\\s+(\\d+)/i) || text.match(/(\\d+)\\s+pages?/i);
                return match ? parseInt(match[1]) : null;
            """)

            if total_pages:
                estimated_pages = total_pages
                print(f"Detected {total_pages} total pages")
            elif estimated_pages:
                print(f"Estimated ~{estimated_pages} pages based on scroll height")

            # Create progress bar - use max_pages if set, otherwise estimated
            pbar_total = max_pages if max_pages else (estimated_pages or 100)
            pbar = tqdm(total=pbar_total, unit="pages", desc="Capturing")

            # Inject a helper script to track images and save them in batches
            # This avoids transferring huge base64 strings for every check
            driver.execute_script("""
                window._boxCapture = {
                    seenHashes: new Set(),
                    newImages: [],

                    getImageHash: function(src) {
                        // Use first 500 chars as hash
                        return src.substring(0, 500);
                    },

                    collectNewImages: function() {
                        var results = [];

                        // Get img elements with data: src
                        var imgs = document.querySelectorAll('img[src^="data:"]');
                        for (var i = 0; i < imgs.length; i++) {
                            var img = imgs[i];
                            var rect = img.getBoundingClientRect();
                            if (img.src.length > 1000 && rect.width > 100 && rect.height > 100) {
                                var hash = this.getImageHash(img.src);
                                if (!this.seenHashes.has(hash)) {
                                    this.seenHashes.add(hash);
                                    results.push({
                                        src: img.src,
                                        width: rect.width,
                                        height: rect.height
                                    });
                                }
                            }
                        }

                        // Also get canvas elements
                        var canvases = document.querySelectorAll('canvas');
                        for (var i = 0; i < canvases.length; i++) {
                            var canvas = canvases[i];
                            var rect = canvas.getBoundingClientRect();
                            if (rect.width > 100 && rect.height > 100) {
                                try {
                                    var dataUrl = canvas.toDataURL('image/png');
                                    if (dataUrl.length > 1000) {
                                        var hash = this.getImageHash(dataUrl);
                                        if (!this.seenHashes.has(hash)) {
                                            this.seenHashes.add(hash);
                                            results.push({
                                                src: dataUrl,
                                                width: rect.width,
                                                height: rect.height
                                            });
                                        }
                                    }
                                } catch(e) {}
                            }
                        }

                        return results;
                    },

                    getSeenCount: function() {
                        return this.seenHashes.size;
                    },

                    scroll: function() {
                        var scrolled = false;
                        var selectors = [
                            '.bp-doc', '[class*="bp-doc"]',
                            '.PreviewContent', '[class*="PreviewContent"]',
                            '[class*="bcpr"]'
                        ];

                        for (var i = 0; i < selectors.length; i++) {
                            var els = document.querySelectorAll(selectors[i]);
                            for (var j = 0; j < els.length; j++) {
                                var el = els[j];
                                if (el.scrollHeight > el.clientHeight) {
                                    el.scrollTop += 800;
                                    scrolled = true;
                                }
                            }
                        }

                        if (!scrolled) {
                            window.scrollBy(0, 800);
                        }

                        return scrolled;
                    }
                };
            """)

            # Main capture loop - optimized
            while no_new_images_count < 10:
                # Check if we've reached max pages
                if max_pages and len(page_images) >= max_pages:
                    pbar.write(f"Reached max pages limit ({max_pages})")
                    break

                # Collect only NEW images (deduplication done in browser)
                new_images = driver.execute_script("return window._boxCapture.collectNewImages();")

                # Process and save new images
                for img_data in new_images:
                    page_num = len(page_images) + 1
                    page_images.append(True)  # Just track count

                    # Save the image
                    try:
                        img_src = img_data['src']
                        header, data = img_src.split(',', 1)
                        ext = 'png' if 'png' in header else 'jpg'
                        filename = os.path.join(output_dir, f"page_{page_num:04d}.{ext}")
                        with open(filename, 'wb') as f:
                            f.write(base64.b64decode(data))
                        pbar.update(1)
                        # Update total if we exceed estimate
                        if estimated_pages and page_num > estimated_pages:
                            pbar.total = page_num
                            pbar.refresh()
                    except Exception as e:
                        pbar.write(f"Error saving page {page_num}: {e}")

                    # Check max_pages after each save
                    if max_pages and len(page_images) >= max_pages:
                        break

                current_count = len(page_images)
                if current_count == last_image_count:
                    no_new_images_count += 1
                else:
                    no_new_images_count = 0
                    last_image_count = current_count

                # Scroll and wait for new content
                driver.execute_script("window._boxCapture.scroll();")

                # Smart wait: check for new images quickly
                wait_start = time.time()
                time.sleep(0.05)  # Minimal wait for scroll to process

                while (time.time() - wait_start) < scroll_pause:
                    new_count = driver.execute_script("return window._boxCapture.getSeenCount();")
                    if new_count > len(page_images):
                        break
                    time.sleep(0.05)

            pbar.close()

        except Exception as e:
            print(f"\nError during capture: {e}")
            import traceback
            traceback.print_exc()

        print(f"Captured {len(page_images)} total pages")
        return len(page_images)

    def clean(self):
        """
        This will close the current selenium session
        """
        self.driver_obj.quit()
