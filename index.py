import os
import re
import time
import tempfile
import traceback
import shutil
import logging
import requests
from threading import Lock

from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# --------- CPU LIMIT (Linux only) ---------
# Limit this process to use only CPU 0 and 1 (adjust as needed)
try:
    os.sched_setaffinity(0, {0, 1})
except AttributeError:
    # Not Linux, skip
    pass

# --------- CONSTANTS ---------
ANILIST_URL = "https://graphql.anilist.co"
MIRURO_WATCH_BASE = "https://www.miruro.to/watch"
MAX_RUNTIME_SECONDS = 600  # 10 minutes max per extraction

# --------- FLASK APP & RATE LIMITER ---------
app = Flask(__name__)
limiter = Limiter(
    app=app,
    key_func=get_remote_address,
    default_limits=["5 per minute"]
)

# --------- LOGGING SETUP ---------
logging.basicConfig(
    filename="server.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

# --------- CONCURRENCY LOCK ---------
extraction_lock = Lock()

# --------- GRAPHQL FETCH ---------
def fetch_anime_details(anime_id: int):
    logging.info(f"Fetching anime details for ID {anime_id} from AniList...")
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
        response = requests.post(ANILIST_URL, json={"query": query, "variables": variables}, timeout=15)
        response.raise_for_status()
        data = response.json()
        logging.info(f"AniList data fetched successfully for ID {anime_id}.")
        return data.get("data", {}).get("Media", None)
    except Exception as e:
        logging.error(f"AniList fetch failed for ID {anime_id}: {e}")
        return None

# --------- SELENIUM DRIVER SETUP ---------
def initialize_driver():
    logging.info("Initializing headless Chrome WebDriver...")
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
    logging.info("Chrome WebDriver initialized.")
    return driver, temp_dir

# --------- VIDEO URL EXTRACTION ---------
def extract_video_url(driver, max_attempts=25):
    logging.info("Extracting video URL...")
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
            logging.info(f"Video URL found: {video_url}")
            return video_url

    logging.warning("No video URL found after multiple attempts.")
    return None

# --------- MIRURO EPISODE DETECTION ---------
def get_miruro_episode_count(driver, anime_id: int):
    logging.info(f"Detecting number of episodes on Miruro for anime {anime_id}...")
    try:
        url = f"{MIRURO_WATCH_BASE}/{anime_id}/episode-1"
        driver.get(url)
        WebDriverWait(driver, 5).until(EC.presence_of_element_located((By.CSS_SELECTOR, "#episodes-list-container button")))
        ep_buttons = driver.find_elements(By.CSS_SELECTOR, "#episodes-list-container button")
        count = len(ep_buttons) if ep_buttons else 0
        logging.info(f"Detected {count} episodes on Miruro for anime {anime_id}.")
        return count
    except Exception as e:
        logging.warning(f"Episode detection failed for anime {anime_id}: {e}")
        return 0

# --------- MAIN EXTRACTION ---------
def extract_miruro_links(anime_id: int):
    with extraction_lock:  # limit concurrent extractions per process
        logging.info(f"Starting extraction for anime ID {anime_id}...")
        start_time = time.time()

        anime = fetch_anime_details(anime_id)
        if not anime:
            logging.error(f"Could not fetch anime details for ID {anime_id}.")
            return {"error": "Could not fetch anime details"}

        total_eps_anilist = min(anime.get("episodes", 12), 25)
        driver, temp_dir = initialize_driver()
        try:
            total_eps_miruro = get_miruro_episode_count(driver, anime_id)
            total_eps = min(total_eps_anilist, total_eps_miruro or total_eps_anilist)
            logging.info(f"Total episodes to extract: {total_eps}")

            results = []
            for ep in range(1, total_eps + 1):
                if time.time() - start_time > MAX_RUNTIME_SECONDS:
                    logging.error("Extraction exceeded max runtime of 10 minutes. Stopping process.")
                    break

                try:
                    logging.info(f"Loading Episode {ep}...")
                    watch_url = f"{MIRURO_WATCH_BASE}/{anime_id}/episode-{ep}"
                    driver.get(watch_url)
                    WebDriverWait(driver, 5).until(lambda d: d.find_elements(By.TAG_NAME, "video") or True)
                    video_url = extract_video_url(driver)
                    if video_url:
                        results.append({"episode": ep, "url": video_url})
                except Exception as e:
                    logging.error(f"Episode {ep} extraction failed: {e}")
                    traceback.print_exc()

            logging.info(f"Extraction completed for anime {anime_id}.")
            return {
                "anime_id": anime_id,
                "title": anime["title"].get("romaji") or anime["title"].get("english") or f"Anime {anime_id}",
                "episodes": results
            }
        finally:
            driver.quit()
            shutil.rmtree(temp_dir, ignore_errors=True)

# --------- HOME ROUTE ---------
@app.route("/", methods=["GET"])
@limiter.limit("5 per minute")  # enforce rate limit per IP
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
    logging.info(f"Starting Flask server on port {port}...")
    app.run(host="0.0.0.0", port=port)
