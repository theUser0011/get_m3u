import os
import re
import time
import traceback
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

# ✅ Import Telegram messaging function
from send_mst import msg_fun, file_fun

# --------- CONSTANTS ---------
ANILIST_URL = "https://graphql.anilist.co"
MIRURO_WATCH_BASE = "https://www.miruro.to/watch"

# --------- GRAPHQL FETCH ---------
def fetch_anime_details(anime_id: int):
    """Fetch anime details (title, desc, cover, etc.) from AniList GraphQL API"""
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
        response = requests.post(ANILIST_URL, json={"query": query, "variables": variables})
        response.raise_for_status()
        data = response.json()
        return data.get("data", {}).get("Media", None)
    except Exception as e:
        msg_fun(f"❌ AniList fetch failed: {e}")
        print(f"[ERROR] Failed to fetch AniList data: {e}")
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

    service = Service("chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    return driver

# --------- VIDEO URL EXTRACTION ---------
def extract_video_url(driver, max_presses=25):
    """Try pressing 'K' key to start video and extract m3u8/mp4 URL"""
    actions = ActionChains(driver)
    body = driver.find_element(By.TAG_NAME, "body")
    pattern_m3u8 = re.compile(r'https?://[^\s"\'<>]+\.m3u8')
    pattern_mp4 = re.compile(r'https?://[^\s"\'<>]+\.mp4')

    for i in range(max_presses):
        try:
            actions.move_to_element(body).click().send_keys("k").perform()
        except Exception:
            pass
        time.sleep(1.2)
        html = driver.page_source

        m3u8_match = pattern_m3u8.search(html)
        mp4_match = pattern_mp4.search(html)

        if m3u8_match or mp4_match:
            return m3u8_match.group(0) if m3u8_match else mp4_match.group(0)
    return None

# --------- MIRURO EPISODE DETECTION ---------
def get_miruro_episode_count(driver, anime_id: int):
    """Detect number of available episodes from Miruro page"""
    try:
        url = f"{MIRURO_WATCH_BASE}/{anime_id}/episode-1"
        driver.get(url)
        time.sleep(2)
        ep_buttons = driver.find_elements(By.CSS_SELECTOR, "#episodes-list-container button")
        if ep_buttons:
            print(f"[INFO] Found {len(ep_buttons)} episodes on Miruro.")
            # Print available episode titles
            for i, btn in enumerate(ep_buttons, start=1):
                title = btn.get_attribute("title") or "Untitled"
                print(f"  • {i}. {title}")
            return len(ep_buttons)
        else:
            print("[WARN] Could not detect episodes on Miruro.")
            return 0
    except Exception as e:
        print(f"[WARN] Episode detection failed: {e}")
        return 0

# --------- HTML RENDER FUNCTION ---------
def render_html_template(template_path, output_path, anime, episodes):
    """Render the HTML template with anime data and episode links."""
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()

        html = html.replace("{{ title }}", anime["title"].get("romaji") or "Untitled")
        html = html.replace("{{ cover_url }}", anime["coverImage"]["extraLarge"])
        html = html.replace("{{ score }}", str(anime.get("averageScore", "N/A")))
        html = html.replace("{{ total_eps }}", str(len(episodes)))

        # Build episode links HTML
        episode_html = ""
        for ep in episodes:
            episode_html += f'<a href="{ep["url"]}" class="btn btn-outline-primary episode-btn" target="_blank">Episode {ep["episode"]}</a>\n'

        # Replace episode loop block
        html = re.sub(r"{% for ep in episodes %}.*?{% endfor %}", episode_html.strip(), html, flags=re.DOTALL)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

    except Exception as e:
        print(f"[ERROR] HTML render failed: {e}")
        msg_fun(f"❌ HTML render failed: {e}")

# --------- MAIN EXTRACTION ---------
def extract_miruro_links(anime_id: int):
    """Extract streaming URLs for all episodes of a Miruro anime"""
    anime = fetch_anime_details(anime_id)
    if not anime:
        msg_fun("❌ Could not fetch anime details.")
        print("[ERROR] Could not fetch anime details from AniList.")
        return

    title = anime["title"].get("romaji") or anime["title"].get("english") or f"Anime {anime_id}"
    total_eps_anilist = anime.get("episodes", 12)
    total_eps_anilist = min(total_eps_anilist, 25)  # avoid long runs

    driver = initialize_driver()

    # ✅ Detect real episode count from Miruro
    total_eps_miruro = get_miruro_episode_count(driver, anime_id)
    if total_eps_miruro == 0:
        print("[WARN] Falling back to AniList episode count.")
        total_eps_miruro = total_eps_anilist

    # ✅ Use the smaller of both to prevent overfetch
    total_eps = min(total_eps_anilist, total_eps_miruro)

    start_msg = f"🎬 Starting extraction for {title} ({total_eps} episodes detected)"
    print(start_msg)
    msg_fun(start_msg)

    results = []

    for ep in range(1, total_eps + 1):
        watch_url = f"{MIRURO_WATCH_BASE}/{anime_id}/episode-{ep}"
        short_msg = f"▶️ Ep {ep}/{total_eps} → {watch_url}"
        print(f"\n[INFO] Loading Episode {ep}: {watch_url}")
        msg_fun(short_msg)

        try:
            driver.get(watch_url)
            time.sleep(1)
            video_url = extract_video_url(driver)
            if video_url:
                results.append({"episode": ep, "url": video_url})
                success_msg = f"✅ Ep {ep}: {video_url}..."
                print(success_msg)
                msg_fun(success_msg)
            else:
                warn_msg = f"⚠️ Ep {ep}: No URL found"
                print(warn_msg)
                msg_fun(warn_msg)
        except Exception as e:
            err_msg = f"❌ Ep {ep} failed: {str(e)[:100]}"
            print(err_msg)
            msg_fun(err_msg)
            traceback.print_exc()

        time.sleep(1.5)

    driver.quit()

    # --------- SAVE RESULTS (NEW HTML TEMPLATE LOGIC ADDED) ---------
    print("\n=== Extraction Completed ===")
    done_msg = f"✅ Extraction completed for {title}. Total: {len(results)} URLs"
    print(done_msg)
    msg_fun(done_msg)

    # ✅ Generate HTML Report
    html_filename = f"miruro_{anime_id}.html"
    render_html_template("template.html", html_filename, anime, results)

    print(f"\n📁 HTML Report generated: {html_filename}")
    msg_fun(f"📁 HTML Report generated: {html_filename}")

    # ✅ Send HTML file to Telegram
    file_fun(html_filename, "HTML Report")

# --------- ENTRY POINT ---------
if __name__ == "__main__":
    user_input = os.getenv("ANIME_ID", "").strip()

    if not user_input:
        user_input = input("Enter AniList ID or Miruro URL: ").strip()

    # Extract ID from Miruro URL if necessary
    if "miruro.to" in user_input:
        match = re.search(r"/watch/(\d+)", user_input)
        if match:
            anime_id = int(match.group(1))
        else:
            msg_fun("❌ Invalid Miruro URL format.")
            print("❌ Invalid Miruro URL format.")
            exit(1)
    else:
        anime_id = int(user_input)

    extract_miruro_links(anime_id)
