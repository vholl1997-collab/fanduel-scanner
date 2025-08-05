import requests
import time

def fetch_odds():
    url = "https://sportsbook.fanduel.com/api/content/v2/us?region=us&locale=en"
    response = requests.get(url)
    if response.status_code != 200:
        print("❌ Failed to fetch data")
        return
    data = response.json()
    for event in data.get("events", []):
        title = event.get("title", "No title")
        print(f"\n📍 {title}")
        for market in event.get("markets", []):
            if market.get("marketType") == "TOTAL_POINTS":
                for outcome in market.get("outcomes", []):
                    print(f"  ➤ {outcome['label']} @ {outcome['oddsAmerican']}")

if __name__ == "__main__":
    while True:
        fetch_odds()
        print("\n--- Refreshing in 60 seconds ---\n")
        time.sleep(60)
