import os
import re
import time
import traceback
import requests
from flask import Flask, request, jsonify
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options

# Optional: Telegram messaging functions
from send_mst import msg_fun, file_fun

# --------- CONSTANTS ---------
ANILIST_URL = "https://graphql.anilist.co"
MIRURO_WATCH_BASE = "https://www.miruro.to/watch"

app = Flask(__name__)

# --------- GRAPHQL FETCH ---------
def fetch_anime_details(anime_id: int):
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
    actions = ActionChains(driver)
    body = driver.find_element(By.TAG_NAME, "body")
    pattern_m3u8 = re.compile(r'https?://[^\s"\'<>]+\.m3u8')
    pattern_mp4 = re.compile(r'https?://[^\s"\'<>]+\.mp4')

    for _ in range(max_presses):
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
    try:
        url = f"{MIRURO_WATCH_BASE}/{anime_id}/episode-1"
        driver.get(url)
        time.sleep(2)
        ep_buttons = driver.find_elements(By.CSS_SELECTOR, "#episodes-list-container button")
        return len(ep_buttons) if ep_buttons else 0
    except Exception as e:
        print(f"[WARN] Episode detection failed: {e}")
        return 0

# --------- MAIN EXTRACTION ---------
def extract_miruro_links(anime_id: int):
    anime = fetch_anime_details(anime_id)
    if not anime:
        return {"error": "Could not fetch anime details"}

    total_eps_anilist = min(anime.get("episodes", 12), 25)
    driver = initialize_driver()
    total_eps_miruro = get_miruro_episode_count(driver, anime_id)
    total_eps = min(total_eps_anilist, total_eps_miruro or total_eps_anilist)

    results = []

    for ep in range(1, total_eps + 1):
        try:
            watch_url = f"{MIRURO_WATCH_BASE}/{anime_id}/episode-{ep}"
            driver.get(watch_url)
            time.sleep(1)
            video_url = extract_video_url(driver)
            if video_url:
                results.append({"episode": ep, "url": video_url})
        except Exception as e:
            print(f"[ERROR] Ep {ep} failed: {e}")
            traceback.print_exc()
        time.sleep(1.0)

    driver.quit()
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

    # Extract ID
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
    app.run(host="0.0.0.0", port=port)
