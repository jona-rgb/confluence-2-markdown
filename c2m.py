import argparse
import os
import requests
from getpass import getpass
from markdownify import MarkdownConverter
from urllib.parse import urlparse, parse_qs, unquote_plus, unquote
import base64
import json
import re
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

def extract_page_info(page_url):
    """
    Identical to your existing function, extended for multiple Confluence URL patterns.
    """
    parsed = urlparse(page_url)
    if "viewpage.action" in parsed.path:
        qs = parse_qs(parsed.query)
        page_id = qs.get("pageId", [None])[0]
        if page_id:
            return None, None, page_id
        space_key = qs.get("spaceKey", [None])[0]
        raw_title = qs.get("title", [None])[0]
        if space_key and raw_title:
            page_title = unquote_plus(raw_title)
            return space_key, page_title, None
        raise ValueError("Neither pageId nor (spaceKey + title) found in the URL query.")
    elif "display" in parsed.path:
        parts = parsed.path.split('/')
        if len(parts) >= 3:
            space_key = parts[2]
            page_title = unquote_plus(parts[3]) if len(parts) >= 4 else ""
            return space_key, page_title, None
        else:
            raise ValueError("PAGE_URL in /display/ format does not have enough parts.")
    elif "wiki" in parsed.path and "spaces" in parsed.path:
        parts = parsed.path.split('/')
        if len(parts) >= 7:
            space_key = parts[3]
            page_title = unquote_plus(parts[6])
            return space_key, page_title, None
        else:
            raise ValueError("PAGE_URL in /wiki/spaces/ format does not have enough parts.")
    else:
        raise ValueError("PAGE_URL does not follow a recognized Confluence URL format.")

def clear_images_folder(folder="images"):
    if os.path.exists(folder):
        for filename in os.listdir(folder):
            file_path = os.path.join(folder, filename)
            try:
                if os.path.isfile(file_path) or os.path.islink(file_path):
                    os.unlink(file_path)
            except Exception as e:
                print(f"Failed to delete {file_path}. Reason: {e}")
    else:
        os.makedirs(folder)

def download_image(image_url, local_path):
    response = requests.get(image_url, headers=headers, verify=False)
    response.raise_for_status()
    with open(local_path, 'wb') as f:
        f.write(response.content)
    print(f"Image downloaded: {local_path}")

def get_drawio_attachment(content_id, diagram_name=None):
    """
    Looking for .png attachments that match a diagram_name.
    """
    att_api = f"{BASE_URL}/rest/api/content/{content_id}/child/attachment?expand=version,container"
    r = requests.get(att_api, headers=headers)
    r.raise_for_status()
    att_data = r.json()

    png_attachments = [a for a in att_data["results"]
                       if a["metadata"].get("mediaType") == "image/png"]

    if not png_attachments:
        return None, None

    if diagram_name:
        possibilities = [diagram_name + ".png", diagram_name + ".drawio.png"]
        for att in png_attachments:
            if att["title"] in possibilities:
                dl_link = att["_links"]["download"]
                return BASE_URL.rstrip("/") + dl_link, att["title"]
        print(f"WARNING: no PNG matched {diagram_name}, returning the first found.")
        first = png_attachments[0]
        dl_link = first["_links"]["download"]
        return BASE_URL.rstrip("/") + dl_link, first["title"]

    # fallback: first
    first = png_attachments[0]
    dl_link = first["_links"]["download"]
    return BASE_URL.rstrip("/") + dl_link, first["title"]

class TwoPassConverter(MarkdownConverter):
    """
    A custom converter that:
      1) Collects headings (H1..H6).
      2) Replaces <div data-macro-name="toc"> with a placeholder (e.g. <<<TOC-0>>>).
      3) Replaces <div data-macro-name="drawio"> with the relevant diagram PNG.
      4) After the entire parse, we do a second pass to fill in the actual TOC(s).
    """

    def __init__(self, **options):
        super().__init__(**options)
        self.headings = []
        self.toc_placeholders = []
        self.last_heading_level = 0  # Track the last heading level we used

    @staticmethod
    def slugify(text):
        """
        Convert heading text to a GitHub-like slug for anchor references.
        E.g. "Background Info" -> "background-info"
        """
        slug = text.strip().lower()
        slug = re.sub(r"[^\w\s-]", "", slug)    # Remove punctuation
        slug = re.sub(r"\s+", "-", slug)        # Spaces -> dashes
        slug = re.sub(r"-+", "-", slug)         # Collapse multiple dashes
        return slug

    #
    # HEADINGS
    #
    def convert_h1(self, el, text, convert_as_inline):
        return self._convert_heading(el, text, level_from_tag=1)

    def convert_h2(self, el, text, convert_as_inline):
        return self._convert_heading(el, text, level_from_tag=2)

    def convert_h3(self, el, text, convert_as_inline):
        return self._convert_heading(el, text, level_from_tag=3)

    def convert_h4(self, el, text, convert_as_inline):
        return self._convert_heading(el, text, level_from_tag=4)

    def convert_h5(self, el, text, convert_as_inline):
        return self._convert_heading(el, text, level_from_tag=5)

    def convert_h6(self, el, text, convert_as_inline):
        return self._convert_heading(el, text, level_from_tag=6)

    def _convert_heading(self, el, heading_text, level_from_tag):
        """
        1) Possibly unify numeric prefixes from data-nh-numbering or <span class="nh-number">
        2) If the heading text already starts with the same number, skip the prefix to avoid duplication.
        3) Clamp heading levels so we don't skip (like going from H2 -> H4).
        """
        # Gather prefix from data-nh-numbering or <span class="nh-number">
        prefix = ""
        # if there's a data-nh-numbering attribute
        attr_prefix = el.attrs.get("data-nh-numbering")
        if attr_prefix:
            # e.g. "3. "
            prefix += attr_prefix

        # if there's a <span class="nh-number"> child
        nh_span = el.find("span", {"class": "nh-number"})
        if nh_span:
            span_txt = nh_span.get_text(strip=True)
            # Optional logic: skip if it's the same as attr_prefix
            # or just append both
            if span_txt and span_txt not in prefix:
                prefix += span_txt

        # Clean up the main heading text. Remove extra newlines, etc.
        heading_text_stripped = heading_text.replace('\u00a0', ' ')
        heading_text_stripped = re.sub(r'\s+', ' ', heading_text_stripped).strip()

        # if heading_text_stripped already starts with the same prefix, skip merging it
        # if heading_text already starts with a digit+dot, let's assume it's got a prefix
        if re.match(r"^\d+(\.\d+)*", heading_text_stripped):
            # heading_text already has a numeric prefix, so skip prefix to avoid duplication
            final_text = heading_text_stripped
        else:
            # else add prefix if it is non-empty
            final_text = (prefix + heading_text_stripped).strip()

        # clamp heading levels so we can't jump from 2 -> 4
        if self.last_heading_level == 0:
            final_level = level_from_tag
        else:
            if level_from_tag > self.last_heading_level + 1:
                final_level = self.last_heading_level + 1
            else:
                final_level = level_from_tag
        self.last_heading_level = final_level

        # store heading for the final TOC
        anchor = self.slugify(final_text)
        self.headings.append((final_level, anchor, final_text))

        # build the markdown heading
        hashes = "#" * final_level
        return f"\n\n{hashes} {final_text}\n\n"

    #
    # IMAGES
    #
    def convert_img(self, el, text, convert_as_inline):
        src = el.attrs.get('data-image-src', el.attrs.get('src'))
        if src and not src.startswith("http"):
            src = BASE_URL.rstrip("/") + src.replace("//", "")
        print("Detected normal image:", src)

        if "status-macro/placeholder" in src:
            return super().convert_img(el, text, convert_as_inline) + "\n\n"

        parsed_src = urlparse(src)
        local_filename = unquote(os.path.basename(parsed_src.path))
        local_path = os.path.join("images", local_filename)
        try:
            download_image(src, local_path)
            el.attrs['src'] = f"./images/{local_filename}"
        except Exception as e:
            print(f"Error downloading image {src}: {e}")
            el.attrs['src'] = src

        return super().convert_img(el, text, convert_as_inline) + "\n\n"

    #
    # LINKS
    #
    def convert_a(self, el, text, convert_as_inline):
        href = el.attrs.get('href', '')
        if not href:
            return super().convert_a(el, text, convert_as_inline)
        if re.match(r'^(https?://|mailto:)', href):
            return super().convert_a(el, text, convert_as_inline)

        href = BASE_URL.rstrip("/") + href
        el.attrs['href'] = href

        return super().convert_a(el, text, convert_as_inline)

    #
    # SPECIAL MACROS
    #
    def convert_div(self, el, text, convert_as_inline):
        macro_name = el.attrs.get("data-macro-name", "").lower()

        # 1) Draw.io
        if macro_name == "drawio":
            return self._convert_drawio_macro(el)

        # 2) TOC
        elif macro_name == "toc":
            # We don't generate the TOC right now; we store a placeholder
            placeholder_index = len(self.toc_placeholders)
            placeholder_text = f"<<<TOC-{placeholder_index}>>>"
            self.toc_placeholders.append(placeholder_text)
            return placeholder_text  # We return this placeholder for now

        # Else normal div
        return f"\n\n{text}\n\n"

    def _convert_drawio_macro(self, el):
        # Find the hidden child <div id="drawio-macro-data-..."> that has Base64 JSON
        macro_data_div = None
        for child in el.children:
            if (
                    hasattr(child, "attrs") and
                    "id" in child.attrs and
                    child.attrs["id"].startswith("drawio-macro-data-")
            ):
                macro_data_div = child
                break

        if not macro_data_div:
            return "\n\n[Error: No draw.io macro-data div found]\n\n"

        raw_b64 = macro_data_div.get_text(strip=True)
        if not raw_b64:
            return "\n\n[Error: draw.io macro-data div is empty]\n\n"

        try:
            decoded_bytes = base64.b64decode(raw_b64)
            macro_json = json.loads(decoded_bytes.decode("utf-8"))
        except Exception as e:
            return f"\n\n[Error decoding draw.io macro data: {e}]\n\n"

        diagram_name = macro_json.get("diagramName", "")
        preview_name = macro_json.get("previewName", "")
        print("Detected draw.io diagramName:", diagram_name, "previewName:", preview_name)

        # Possibly read style from the div
        drawio_macro_div = el.find("div", {"class": "drawio-macro"})
        width_px, height_px = None, None
        if drawio_macro_div:
            style_str = drawio_macro_div.attrs.get("style", "")
            w_match = re.search(r"width:(\d+)px", style_str)
            h_match = re.search(r"height:(\d+)px", style_str)
            if w_match:
                width_px = w_match.group(1)
            if h_match:
                height_px = h_match.group(1)

        # Download the PNG
        download_url, att_title = get_drawio_attachment(page_id, diagram_name=diagram_name)
        if not download_url:
            return "\n\n[Drawio diagram attachment not found]\n\n"

        parsed_url = urlparse(download_url)
        local_filename = unquote(os.path.basename(parsed_url.path))
        local_path = os.path.join("images", local_filename)

        try:
            download_image(download_url, local_path)
        except Exception as e:
            return f"\n\n[Error downloading draw.io attachment: {e}]\n\n"

        md_str = f"![{att_title}](./images/{local_filename})"
        style_parts = []
        if width_px:
            style_parts.append(f"width: {width_px}px;")
        if height_px:
            style_parts.append(f"height: {height_px}px;")
        if style_parts:
            style_str = " ".join(style_parts)
            md_str += f'{{: style="{style_str}"}}'

        return f"\n\n{md_str}\n\n"

    def finalize_toc(self, text):
        """
        After we have fully parsed the HTML, we have:
          - self.headings = all discovered headings
          - self.toc_placeholders = e.g. ["<<<TOC-0>>>", "<<<TOC-1>>>", ...]

        This method replaces each placeholder with a bullet list referencing the headings.
        """
        if not self.toc_placeholders:
            return text  # no toc macros found

        # Build a single TOC string from self.headings
        all_headings = self.headings  # list of (level, anchor, text)
        if not all_headings:
            # no headings discovered, so just say so
            toc_markdown = "\n\n(No headings found for TOC)\n\n"
        else:
            # Example bullet list
            lines = []
            # lines.append("## Table of Contents\n")
            for (level, anchor, heading_text) in all_headings:
                if level == 1:
                    indent = ""
                else:
                    indent = "  " * (level-1)
                line = f"{indent}- [{heading_text}](#{anchor})"
                lines.append(line)
            toc_markdown = "\n".join(lines) + "\n\n"

        for placeholder in self.toc_placeholders:
            text = text.replace(placeholder, toc_markdown)

        return text

def custom_md(html_content):
    """
    1. We parse the HTML with TwoPassConverter to get an intermediate Markdown string with placeholders.
    2. Then we do a finalize_toc() step to fill placeholders with the actual bullet list of headings.
    """
    converter = TwoPassConverter()
    intermediate_md = converter.convert(html_content)
    final_md = converter.finalize_toc(intermediate_md)
    return final_md

# ----------------- MAIN SCRIPT -----------------
parser = argparse.ArgumentParser(description="Confluence to Markdown Converter")
# Flag to force manual input
parser.add_argument(
    '--manual',
    action='store_true',
    help='Force manual input even if environment variables are set'
)
args = parser.parse_args()

if args.manual:
    PAGE_URL = input("Enter the Confluence page URL: ")
    BEARER_TOKEN = getpass("Enter your Confluence API token: ")
else:
    PAGE_URL = os.getenv('PAGE_URL')
    BEARER_TOKEN = os.getenv('BEARER_TOKEN')
    if not PAGE_URL or not BEARER_TOKEN:
        print("No environment variables found.")
        print("Please set the PAGE_URL and BEARER_TOKEN environment variables or use --manual.")
        exit(1)

parsed_url = urlparse(PAGE_URL)
BASE_URL = f"{parsed_url.scheme}://{parsed_url.netloc}"
print("Detected BASE_URL:", BASE_URL)

space_key, page_title, page_id = extract_page_info(PAGE_URL)
if page_id:
    print("Extracted pageId:", page_id)
else:
    print("Extracted SPACE_KEY:", space_key)
    print("Extracted PAGE_TITLE:", page_title)

headers = {
    "Authorization": f"Bearer {BEARER_TOKEN}",
    "Accept": "application/json"
}

clear_images_folder("images")

CONTENT_API = f"{BASE_URL}/rest/api/content"

# Retrieve the page HTML
if page_id:
    api_url = f"{CONTENT_API}/{page_id}?expand=space,body.view,version,container"
    response = requests.get(api_url, headers=headers)
else:
    params = {
        "spaceKey": space_key,
        "title": page_title,
        "expand": "space,body.view,version,container"
    }
    response = requests.get(CONTENT_API, headers=headers, params=params)

response.raise_for_status()
data = response.json()

# If valid page data
if (page_id and "id" in data) or (data.get("size", 0) > 0):
    result = data if page_id else data["results"][0]
    html_content = result["body"]["view"]["value"]

    if not page_id:
        page_id = result["id"]
    if not page_title:
        page_title = result.get("title", "")

    # 1) Convert HTML -> Markdown with placeholders for TOC
    # 2) Then replace placeholders with an actual bullet list referencing discovered headings
    converted_markdown = custom_md(html_content)
    markdown_content = f"# {page_title}\n\n" + converted_markdown

    # Save the Markdown content to a file named after the page title
    with open("{0}.md".format(page_title) , "w", encoding="utf-8") as f:
        f.write(markdown_content)

    print("Markdown saved in {0}.md".format(page_title))
else:
    print("No page found.")