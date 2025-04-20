# Confluence-2-markdown

A Python script to retrieve Confluence page content via REST API and convert it to Markdown, including:

- **Image downloads** (automatically storing images in a local `images/` folder and rewriting image references).
- **draw.io diagram conversion** (extracting diagrams from Confluence macros and linking them as images in Markdown).
- **Optional** size handling for diagrams (adding `{: style="width:NNNpx; height:MMMpx;"}` for MkDocs or other Markdown engines that support this syntax).

**If you want to use the script, you can run it locally by following the instructions below.**

## Requirements

- Python 3.7+ (should work on most Python 3 versions).
- `pip3 install -r requirements.txt`  
  (the requirements file usually includes `requests`, `beautifulsoup4`, `markdownify`, etc.)

## Usage

1. **Set up your environment**
   ```bash
   pip3 install -r requirements.txt
    ```
2. **Run the script locally:**
    ```bash
    python3 c2m.py --manual
    ```
> **Note:** The `--manual` flag is used to run the script in manual mode, which prompts the user to enter the Confluence API Token and Page URL.
> Without this flag the script will first try to read the Confluence API Token and Page URL from the environment variables `BEARER_TOKEN` and `PAGE_URL` and as a fallback it will ask you to enter them manually.
3. **Provide the Confluence API Token and the PageUrl**
    ```bash
    Enter the Confluence API Token: 
    Enter the Confluence Page URL: 
    ```
4. **The script will download the images and convert the Confluence page to Markdown. The output will be saved in a file named after the Confluence page title (e.g. `Some Data Model.md`).**
