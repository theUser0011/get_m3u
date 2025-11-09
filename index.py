import os
import re
import time
import tempfile
import traceback
import requests
from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# --------- CONSTANTS ---------
ANILIST_URL = "https://graphql.anilist.co"
MIRURO_WATCH_BASE = "https://www.miruro.to/watch"
MAX_RUNTIME_SECONDS = 600  # 10 minutes max for extraction

app = Flask(__name__)

# --------- GRAPHQL FETCH ---------
def fetch_anime_details(anime_id: int):
    print(f"[LOG] Fetching anime details for ID {anime_id} from AniList...")
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
        print(f"[LOG] AniList data fetched successfully.")
        return data.get("data", {}).get("Media", None)
    except Exception as e:
        print(f"[ERROR] Failed to fetch AniList data: {e}")
        print(f"❌ AniList fetch failed: {e}")
        return None

# --------- SELENIUM DRIVER SETUP ---------
def initialize_driver():
    print("[LOG] Initializing headless Chrome WebDriver...")
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--mute-audio")
    temp_dir = tempfile.mkdtemp()
    options.add_argument(f"--user-data-dir={temp_dir}")

    service = Service("chromedriver")
    driver = webdriver.Chrome(service=service, options=options)
    print("[LOG] Chrome WebDriver initialized.")
    return driver

# --------- VIDEO URL EXTRACTION (Dynamic Wait) ---------
def extract_video_url(driver, max_attempts=25):
    print("[LOG] Extracting video URL...")
    actions = ActionChains(driver)
    body = driver.find_element(By.TAG_NAME, "body")
    pattern_m3u8 = re.compile(r'https?://[^\s"\'<>]+\.m3u8')
    pattern_mp4 = re.compile(r'https?://[^\s"\'<>]+\.mp4')

    for _ in range(max_attempts):
        try:
            actions.move_to_element(body).click().send_keys("k").perform()
        except Exception:
            pass

        try:
            html = WebDriverWait(driver, 2).until(lambda d: d.page_source)
        except:
            html = driver.page_source

        m3u8_match = pattern_m3u8.search(html)
        mp4_match = pattern_mp4.search(html)
        if m3u8_match or mp4_match:
            video_url = m3u8_match.group(0) if m3u8_match else mp4_match.group(0)
            print(f"[LOG] Video URL found: {video_url}")
            return video_url

    print("[WARN] No video URL found after multiple attempts.")
    return None

# --------- MIRURO EPISODE DETECTION ---------
def get_miruro_episode_count(driver, anime_id: int):
    print(f"[LOG] Detecting number of episodes on Miruro for anime {anime_id}...")
    try:
        url = f"{MIRURO_WATCH_BASE}/{anime_id}/episode-1"
        driver.get(url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#episodes-list-container button")))
        ep_buttons = driver.find_elements(By.CSS_SELECTOR, "#episodes-list-container button")
        count = len(ep_buttons) if ep_buttons else 0
        print(f"[LOG] Detected {count} episodes on Miruro.")
        return count
    except Exception as e:
        print(f"[WARN] Episode detection failed: {e}")
        return 0

# --------- MAIN EXTRACTION (With Timeout) ---------
def extract_miruro_links(anime_id: int):
    print(f"[LOG] Starting extraction process for anime ID {anime_id}...")
    start_time = time.time()

    anime = fetch_anime_details(anime_id)
    if not anime:
        print("[ERROR] Could not fetch anime details.")
        return {"error": "Could not fetch anime details"}

    total_eps_anilist = min(anime.get("episodes", 12), 25)
    driver = initialize_driver()
    total_eps_miruro = get_miruro_episode_count(driver, anime_id)
    total_eps = min(total_eps_anilist, total_eps_miruro or total_eps_anilist)
    print(f"[LOG] Total episodes to extract: {total_eps}")

    results = []
    for ep in range(2, total_eps + 1):
        if time.time() - start_time > MAX_RUNTIME_SECONDS:
            print("[ERROR] Extraction exceeded max runtime of 10 minutes. Stopping process.")
            print(f"❌ Extraction for anime {anime_id} stopped due to timeout.")
            break

        try:
            print(f"[LOG] Loading Episode {ep}...")
            watch_url = f"{MIRURO_WATCH_BASE}/{anime_id}/episode-{ep}"
            driver.get(watch_url)
            # Wait until video tag appears or 5 seconds max
            WebDriverWait(driver, 5).until(lambda d: d.find_elements(By.TAG_NAME, "video") or True)
            video_url = extract_video_url(driver)
            if video_url:
                results.append({"episode": ep, "url": video_url})
        except Exception as e:
            print(f"[ERROR] Episode {ep} extraction failed: {e}")
            traceback.print_exc()

    driver.quit()

    # Stop process if too few episodes extracted and episodes <=12
    if len(results) <= 12 and total_eps <= 12:
        print(f"[WARN] Extracted {len(results)} episodes out of {total_eps}. This may be correct for short series.")


    print("[LOG] Extraction completed.")
    return {
        "anime_id": anime_id,
        "title": anime["title"].get("romaji") or anime["title"].get("english") or f"Anime {anime_id}",
        "episodes": results
    }

# --------- HOME ROUTE ---------
@app.route("/", methods=["GET"])
def home():
    anime_input = request.args.get("anime_id") or request.args.get("id") or request.args.get("url")
    if not anime_input:
        return jsonify({"message": "Welcome! Provide ?anime_id=<id> to get video URLs."}), 200

    if "miruro.to" in anime_input:
        match = re.search(r"/watch/(\d+)", anime_input)
        if not match:
            return jsonify({"error": "Invalid Miruro URL format"}), 400
        anime_id = int(match.group(1))
    else:
        try:
            anime_id = int(anime_input)
        except ValueError:
            return jsonify({"error": "Invalid AniList ID"}), 400

    data = extract_miruro_links(anime_id)
    return jsonify(data)

# --------- ENTRY POINT ---------
if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"[LOG] Starting Flask server on port {port}...")
    app.run(host="0.0.0.0", port=port)
