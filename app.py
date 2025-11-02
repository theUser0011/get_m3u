import os
import re
import time
import traceback
import requests
import html
import datetime
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

# ✅ Import Telegram messaging & file sending functions
from send_mst import msg_fun, file_fun

# --------- CONSTANTS ---------
ANILIST_URL = "https://graphql.anilist.co"
MIRURO_WATCH_BASE = "https://www.miruro.to/watch"
TEMPLATE_FILE = "template.html"  # External HTML template

# --------- GRAPHQL FETCH ---------
def fetch_anime_details(anime_id: int):
    """Fetch anime details from AniList GraphQL API"""
    query = """
    query ($id: Int) {
      Media(id: $id, type: ANIME) {
        id
        title {
          romaji
          english
          native
        }
        episodes
        coverImage {
          extraLarge
        }
        averageScore
      }
    }
    """
    variables = {"id": anime_id}
    try:
        response = requests.post(ANILIST_URL, json={"query": query, "variables": variables}, timeout=10)
        response.raise_for_status()
        data = response.json()
        return data.get("data", {}).get("Media")
    except Exception as e:
        msg_fun(f"❌ AniList fetch failed: {e}")
        print(f"[ERROR] AniList API error: {e}")
        return None


# --------- SELENIUM DRIVER SETUP ---------
def initialize_driver():
    """Initialize headless Chrome WebDriver"""
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--mute-audio")
    options.add_argument("--disable-extensions")
    options.add_argument("--disable-infobars")
    options.add_argument("--disable-notifications")

    service = Service("chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    driver.set_page_load_timeout(30)
    return driver


# --------- VIDEO URL EXTRACTION ---------
def extract_video_url(driver, max_presses=25):
    """Press 'K' to play and extract m3u8 or mp4 URL"""
    actions = ActionChains(driver)
    body = driver.find_element(By.TAG_NAME, "body")
    pattern_m3u8 = re.compile(r'https?://[^\s"\'<>]+\.m3u8')
    pattern_mp4 = re.compile(r'https?://[^\s"\'<>]+\.mp4')

    for _ in range(max_presses):
        try:
            actions.move_to_element(body).click().send_keys("k").perform()
        except:
            pass
        time.sleep(1.2)
        source = driver.page_source

        if match := pattern_m3u8.search(source):
            return match.group(0)
        if match := pattern_mp4.search(source):
            return match.group(0)
    return None


# --------- FILENAME SANITIZATION ---------
def sanitize_filename(name: str) -> str:
    """Make string safe for filenames"""
    return re.sub(r'[^a-zA-Z0-9_-]', '_', name).strip('_').lower()


# --------- HTML GENERATION (EXTERNAL TEMPLATE) ---------
def generate_html_file(anime, results):
    """Generate stunning HTML using template.html"""
    title_en = anime["title"].get("english")
    title_romaji = anime["title"].get("romaji")
    title = title_en or title_romaji or f"Anime_{anime['id']}"
    cover = anime["coverImage"]["extraLarge"]
    score = anime.get("averageScore") or "N/A"
    total_eps = len(results)
    sanitized = sanitize_filename(title)
    html_file = f"{sanitized}.html"

    # Check template
    if not os.path.exists(TEMPLATE_FILE):
        msg_fun("❌ template.html not found! Creating fallback...")
        print("❌ template.html missing. Run: touch template.html and paste the template.")
        return None

    with open(TEMPLATE_FILE, "r", encoding="utf-8") as f:
        template = f.read()

    # Build episode cards
    episode_cards = ""
    for r in results:
        ep_num = r["episode"]
        url = html.escape(r["url"])
        card = f"""
        <div class="episode-card">
          <div class="ep-header">Episode {ep_num}</div>
          <div class="video-container">
            <video controls preload="metadata" data-src="{url}" poster="{cover}"></video>
          </div>
          <div class="fallback-link">
            🔗 <a href="{url}" target="_blank">Open Stream</a>
          </div>
        </div>
        """
        episode_cards += card

    # Replace placeholders
    today = datetime.datetime.now().strftime("%B %d, %Y")
    html_content = (
        template
        .replace("{{TITLE}}", html.escape(title))
        .replace("{{COVER}}", cover)
        .replace("{{SCORE}}", str(score))
        .replace("{{EPISODES}}", str(total_eps))
        .replace("{{EPISODE_LIST}}", episode_cards)
        .replace("{{DATE}}", today)
    )

    # Save HTML
    with open(html_file, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"✨ HTML Generated: {html_file}")
    msg_fun(f"✨ HTML Ready: {html_file}")
    file_fun(html_file, f"🎬 {title} - Watch All Episodes")
    return html_file


# --------- MAIN EXTRACTION ---------
def extract_miruro_links(anime_id: int):
    """Extract all episode streaming URLs"""
    anime = fetch_anime_details(anime_id)
    if not anime:
        msg_fun("❌ Anime not found on AniList.")
        return

    title = (anime["title"].get("english") or anime["title"].get("romaji") or f"ID {anime_id}")
    total_eps = min(anime.get("episodes") or 12, 50)
    print(f"🎬 Extracting: {title} ({total_eps} episodes)")
    msg_fun(f"🎬 Starting: {title}")

    driver = initialize_driver()
    results = []

    for ep in range(1, total_eps + 1):
        url = f"{MIRURO_WATCH_BASE}/{anime_id}/episode-{ep}"
        print(f"\n[EP {ep}] Loading: {url}")
        msg_fun(f"⏳ Ep {ep}/{total_eps}")

        try:
            driver.get(url)
            time.sleep(2)
            video_url = extract_video_url(driver)

            if video_url:
                results.append({"episode": ep, "url": video_url})
                print(f"✅ Ep {ep}: {video_url[:70]}...")
                msg_fun(f"✅ Ep {ep} Found!")
            else:
                print(f"⚠️ Ep {ep}: No stream detected")
                msg_fun(f"⚠️ Ep {ep}: Not found")
        except Exception as e:
            print(f"❌ Ep {ep} failed: {e}")
            msg_fun(f"❌ Ep {ep} error")
            traceback.print_exc()

        time.sleep(1.5)

    driver.quit()

    # Save TXT
    txt_file = f"miruro_{anime_id}_links.txt"
    with open(txt_file, "w", encoding="utf-8") as f:
        for r in results:
            f.write(f"Episode {r['episode']}: {r['url']}\n")
    file_fun(txt_file, f"📄 {title} Links")

    # Generate HTML
    if results:
        generate_html_file(anime, results)
    else:
        msg_fun("⚠️ No videos found. HTML skipped.")

    msg_fun(f"✅ Done! {len(results)} episodes ready.")
    print(f"\n✅ All done! {len(results)} episodes extracted.")

import json
# --------- ENTRY POINT ---------
if __name__ == "__main__":
    with open("anime_id.json",encoding='utf-8')as f:
        id_obj = json.load(f)
    user_input = id_obj.get("ANIME_ID", None)

    if not user_input:
        msg_fun("No id provided...")

    # Parse ID from URL
    if "miruro.to" in user_input:
        match = re.search(r"/watch/(\d+)", user_input)
        if not match:
            msg_fun("❌ Invalid Miruro URL")
            exit(1)
        anime_id = int(match.group(1))
    else:
        try:
            anime_id = int(user_input)
        except:
            msg_fun("❌ Please enter a number or valid URL")
            exit(1)

    extract_miruro_links(anime_id)
