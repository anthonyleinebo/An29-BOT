# An29 BOT 💻

A modern Discord music bot built with [discord.py](https://github.com/Rapptz/discord.py) and [yt-dlp](https://github.com/yt-dlp/yt-dlp).  
The bot plays music from YouTube with slash commands and will find and pick highest audio quality available.

---

## ✨ Features
- ▶️ `/play <query or link>` – Play a song from YouTube (link or search term)  
- 📜 `/queue` – Show the current song and upcoming songs  
- ⏭️ `/skip` – Skip the current track  
- ⏹️ `/stop` – Stop playback and disconnect from voice  
- ⏸️ `/pause` – Pause playback  
- ▶️ `/resume` – Resume playback  
- 🔊 `/volume <0.0–1.5>` – Adjust volume (applies to new songs)  
- 🔗 `/join` – Make the bot join your voice channel without playing  
- 🏓 `/ping` – Pong! Quick latency test  
- ℹ️ `/help` – Display a list of available commands  

---

## 🚀 Getting Started

1. **Clone the repo**
   ```bash
   git clone https://github.com/anthonyleinebo/An29-BOT.git
   cd An29-BOT
   ```

2. **Install dependencies***
    ```bash
    pip install -r requirements.txt
    ```

3. **Update the .env file**
    ```bash
    DISCORD_TOKEN=your_bot_token_here
    #Cuz I aint givin you mine
    ```

4. **Run the bot**
    ```bash
    python bot.py
    ```

---

## 🛠️ Requirements
- Python 3.10+
- FFmpeg
 installed and added to PATH
- A Discord bot token (create one in the Discord Developer Portal)

---

## 📦 Dependencies
Listed in `requirements.txt`:

```txt
discord.py[voice]==2.4.0
yt-dlp>=2025.1.1
python-dotenv>=1.0.1
```
---

## 👤 Credits  
Developed with ❤️ by **Anthony Leinebø**  

---

# 📜 License  
This project is licensed under the **Creative Commons Attribution-NonCommercial 4.0 International License (CC BY-NC 4.0)**.  

You are free to:  
- **Share** — copy and redistribute the code in any medium or format  
- **Adapt** — remix, transform, and build upon the code  

Under the following terms:  
- **Attribution** — You must give proper credit to the original author.  
- **NonCommercial** — You may not use the bot or its code for commercial purposes.  

Full license text: [LICENSE](LICENSE)  