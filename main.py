import os
import re
import time
import pytz
import json
import logging
import requests
from datetime import datetime, timedelta
from pytube import YouTube, Playlist
from io import BytesIO
from threading import Thread
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaVideo,
    InputMediaAudio,
)
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    CallbackContext,
    CallbackQueryHandler,
)
from dotenv import load_dotenv
from yt_dlp import YoutubeDL

# Set up logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

# Bot configuration
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else None
SUBSCRIPTION_PRICE = os.getenv("SUBSCRIPTION_PRICE", "5.00")
PAYMENT_INFO = os.getenv("PAYMENT_INFO", "PayPal: example@example.com")

# Subscription management
SUBSCRIPTION_FILE = "subscriptions.json"

# Initialize subscriptions file if not exists
if not os.path.exists(SUBSCRIPTION_FILE):
    with open(SUBSCRIPTION_FILE, "w") as f:
        json.dump({"users": {}}, f)

def load_subscriptions():
    """Load subscriptions from JSON file"""
    with open(SUBSCRIPTION_FILE, "r") as f:
        return json.load(f)

def save_subscriptions(data):
    """Save subscriptions to JSON file"""
    with open(SUBSCRIPTION_FILE, "w") as f:
        json.dump(data, f, indent=4)

def is_subscribed(user_id):
    """Check if user is subscribed"""
    if user_id == ADMIN_ID:
        return True
    
    subscriptions = load_subscriptions()
    user_data = subscriptions["users"].get(str(user_id), {})
    
    if not user_data:
        return False
    
    expiry_date = datetime.strptime(user_data["expiry"], "%Y-%m-%d")
    return expiry_date > datetime.now()

def add_subscription(user_id, days=30):
    """Add or extend user subscription"""
    subscriptions = load_subscriptions()
    expiry_date = datetime.now() + timedelta(days=days)
    
    subscriptions["users"][str(user_id)] = {
        "expiry": expiry_date.strftime("%Y-%m-%d"),
        "plan": f"{days} days"
    }
    
    save_subscriptions(subscriptions)

def search_youtube(query, max_results=10):
    """Search YouTube and return results"""
    base_url = "https://www.youtube.com/results?"
    params = {"search_query": query}
    url = base_url + requests.compat.urlencode(params)
    
    response = requests.get(url)
    if response.status_code != 200:
        return []
    
    video_ids = re.findall(r"watch\?v=(\S{11})", response.text)
    unique_video_ids = list(dict.fromkeys(video_ids))[:max_results]
    
    videos = []
    for video_id in unique_video_ids:
        try:
            yt = YouTube(f"https://www.youtube.com/watch?v={video_id}")
            videos.append({
                "id": video_id,
                "title": yt.title,
                "thumbnail": yt.thumbnail_url,
                "duration": yt.length,
                "url": f"https://www.youtube.com/watch?v={video_id}"
            })
        except Exception as e:
            logger.error(f"Error getting video info: {e}")
            continue
    
    return videos

def format_duration(seconds):
    """Format seconds into HH:MM:SS"""
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    
    if hours > 0:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"

def download_progress(stream, chunk, bytes_remaining, context, chat_id, message_id):
    """Callback for download progress"""
    total_size = stream.filesize
    bytes_downloaded = total_size - bytes_remaining
    percentage = (bytes_downloaded / total_size) * 100
    
    # Calculate download speed
    current_time = time.time()
    if hasattr(context, 'download_start_time'):
        elapsed_time = current_time - context.download_start_time
        if elapsed_time > 0:
            download_speed = bytes_downloaded / elapsed_time
            speed_text = f"Speed: {download_speed / 1024:.2f} KB/s"
        else:
            speed_text = "Calculating speed..."
    else:
        context.download_start_time = current_time
        speed_text = "Starting download..."
    
    # Create progress bar
    progress_bar_length = 20
    filled_length = int(progress_bar_length * percentage // 100)
    progress_bar = '█' * filled_length + '-' * (progress_bar_length - filled_length)
    
    # Edit message with progress
    try:
        context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=message_id,
            text=f"Downloading...\n\n"
                 f"{progress_bar} {percentage:.1f}%\n"
                 f"{speed_text}\n"
                 f"Downloaded: {bytes_downloaded / (1024 * 1024):.2f} MB / {total_size / (1024 * 1024):.2f} MB"
        )
    except Exception as e:
        logger.error(f"Error updating progress: {e}")

def start(update: Update, context: CallbackContext):
    """Handle /start command"""
    user_id = update.effective_user.id
    if is_subscribed(user_id):
        update.message.reply_text(
            "🎬 Welcome to YouTube Downloader Bot!\n\n"
            "You can:\n"
            "- Send a YouTube URL to download\n"
            "- Use /search to find videos\n"
            "- Use /playlist to download a playlist\n\n"
            "Enjoy downloading!"
        )
    else:
        keyboard = [
            [InlineKeyboardButton("Subscribe Now", callback_data="subscribe")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        update.message.reply_text(
            "🎬 Welcome to YouTube Downloader Bot!\n\n"
            "This bot requires a subscription to use.\n"
            f"Price: ${SUBSCRIPTION_PRICE} per month\n"
            f"Payment method: {PAYMENT_INFO}\n\n"
            "After payment, send the receipt to the admin.",
            reply_markup=reply_markup
        )

def search(update: Update, context: CallbackContext):
    """Handle /search command"""
    user_id = update.effective_user.id
    if not is_subscribed(user_id):
        update.message.reply_text("Please subscribe to use this feature.")
        return
    
    if not context.args:
        update.message.reply_text("Please provide a search query. Example: /search funny cats")
        return
    
    query = " ".join(context.args)
    update.message.reply_text(f"🔍 Searching YouTube for: {query}...")
    
    videos = search_youtube(query)
    if not videos:
        update.message.reply_text("No videos found. Try a different search term.")
        return
    
    keyboard = []
    for i, video in enumerate(videos[:10], start=1):
        keyboard.append([
            InlineKeyboardButton(
                f"{i}. {video['title']} ({format_duration(video['duration'])})",
                callback_data=f"select_{video['id']}"
            )
        ])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    update.message.reply_text(
        "Top 10 Search Results:",
        reply_markup=reply_markup
    )

def handle_video_selection(update: Update, context: CallbackContext):
    """Handle video selection from search results"""
    query = update.callback_query
    query.answer()
    
    user_id = update.effective_user.id
    if not is_subscribed(user_id):
        query.edit_message_text("Please subscribe to use this feature.")
        return
    
    video_id = query.data.split("_")[1]
    try:
        yt = YouTube(f"https://www.youtube.com/watch?v={video_id}")
        
        # Get available streams
        streams = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc()
        audio_streams = yt.streams.filter(only_audio=True, file_extension='mp4').order_by('abr').desc()
        
        # Create quality buttons
        keyboard = []
        
        # Video options
        video_options = []
        for stream in streams:
            if stream.resolution not in [s.resolution for s in video_options]:
                video_options.append(stream)
        
        for stream in video_options:
            keyboard.append([
                InlineKeyboardButton(
                    f"🎥 {stream.resolution} ({stream.mime_type.split('/')[1]})",
                    callback_data=f"download_video_{video_id}_{stream.itag}"
                )
            ])
        
        # Audio options
        audio_options = []
        for stream in audio_streams:
            if stream.abr not in [s.abr for s in audio_options]:
                audio_options.append(stream)
        
        for stream in audio_options:
            keyboard.append([
                InlineKeyboardButton(
                    f"🔊 Audio ({stream.abr})",
                    callback_data=f"download_audio_{video_id}_{stream.itag}"
                )
            ])
        
        # Add playlist option if available
        if yt.vid_info.get('playlist'):
            keyboard.append([
                InlineKeyboardButton(
                    "📋 Download Entire Playlist",
                    callback_data=f"playlist_{yt.vid_info['playlist'][0]}"
                )
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        query.edit_message_text(
            f"Select download option for:\n\n"
            f"📹 {yt.title}\n"
            f"⏱ {format_duration(yt.length)}\n"
            f"👁 {yt.views:,} views",
            reply_markup=reply_markup
        )
    except Exception as e:
        query.edit_message_text(f"❌ Error: {str(e)}")
        logger.error(f"Error in handle_video_selection: {e}")

def handle_download(update: Update, context: CallbackContext):
    """Handle download requests"""
    query = update.callback_query
    query.answer()
    
    user_id = update.effective_user.id
    if not is_subscribed(user_id):
        query.edit_message_text("Please subscribe to use this feature.")
        return
    
    data = query.data.split("_")
    download_type = data[1]
    video_id = data[2]
    itag = data[3]
    
    try:
        yt = YouTube(
            f"https://www.youtube.com/watch?v={video_id}",
            on_progress_callback=lambda stream, chunk, bytes_remaining: download_progress(
                stream, chunk, bytes_remaining, context, query.message.chat_id, query.message.message_id
            )
        )
        
        stream = yt.streams.get_by_itag(itag)
        
        # Send initial download message
        context.bot.edit_message_text(
            chat_id=query.message.chat_id,
            message_id=query.message.message_id,
            text=f"⏳ Preparing to download: {yt.title}\n\n"
                 f"Format: {stream.resolution if hasattr(stream, 'resolution') else stream.abr}\n"
                 f"Size: {stream.filesize_mb:.2f} MB\n\n"
                 f"Starting download..."
        )
        
        # Start download in a separate thread
        def download_and_send():
            try:
                # Start download
                buffer = BytesIO()
                stream.stream_to_buffer(buffer)
                buffer.seek(0)
                
                # Send file
                if download_type == "video":
                    context.bot.send_video(
                        chat_id=query.message.chat_id,
                        video=buffer,
                        caption=f"🎥 {yt.title}\n\n"
                               f"Quality: {stream.resolution}\n"
                               f"Duration: {format_duration(yt.length)}",
                        timeout=300
                    )
                else:
                    context.bot.send_audio(
                        chat_id=query.message.chat_id,
                        audio=buffer,
                        caption=f"🔊 {yt.title}\n\n"
                               f"Quality: {stream.abr}\n"
                               f"Duration: {format_duration(yt.length)}",
                        timeout=300
                    )
                
                # Delete progress message
                context.bot.delete_message(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id
                )
            except Exception as e:
                context.bot.edit_message_text(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    text=f"❌ Error downloading video: {str(e)}"
                )
                logger.error(f"Download error: {e}")
        
        Thread(target=download_and_send).start()
    except Exception as e:
        query.edit_message_text(f"❌ Error: {str(e)}")
        logger.error(f"Error in handle_download: {e}")

def playlist(update: Update, context: CallbackContext):
    """Handle /playlist command"""
    user_id = update.effective_user.id
    if not is_subscribed(user_id):
        update.message.reply_text("Please subscribe to use this feature.")
        return
    
    if not context.args:
        update.message.reply_text("Please provide a playlist URL. Example: /playlist https://youtube.com/playlist?list=...")
        return
    
    url = context.args[0]
    if "list=" not in url:
        update.message.reply_text("Invalid playlist URL. Please provide a valid YouTube playlist URL.")
        return
    
    try:
        pl = Playlist(url)
        update.message.reply_text(f"📋 Found playlist: {pl.title}\n\nTotal videos: {len(pl.videos)}")
        
        keyboard = [
            [InlineKeyboardButton("Download All Videos", callback_data=f"download_playlist_{pl.playlist_id}")],
            [InlineKeyboardButton("Download as Audio", callback_data=f"download_playlist_audio_{pl.playlist_id}")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text("Select download option:", reply_markup=reply_markup)
    except Exception as e:
        update.message.reply_text(f"Error: {str(e)}")
        logger.error(f"Playlist error: {e}")

def handle_playlist_download(update: Update, context: CallbackContext):
    """Handle playlist download requests"""
    query = update.callback_query
    query.answer()
    
    user_id = update.effective_user.id
    if not is_subscribed(user_id):
        query.edit_message_text("Please subscribe to use this feature.")
        return
    
    data = query.data.split("_")
    download_type = data[2] if data[1] == "playlist" else "video"  # Handle both cases
    playlist_id = data[3]
    
    try:
        pl = Playlist(f"https://www.youtube.com/playlist?list={playlist_id}")
        total_videos = len(pl.videos)
        
        query.edit_message_text(f"⏳ Preparing to download playlist: {pl.title}\n\nTotal videos: {total_videos}\n\nStarting download...")
        
        # Start download in a separate thread
        def download_playlist():
            try:
                success_count = 0
                for i, video in enumerate(pl.videos, start=1):
                    try:
                        yt = YouTube(video)
                        
                        if download_type == "audio":
                            stream = yt.streams.get_audio_only()
                        else:
                            stream = yt.streams.get_highest_resolution()
                        
                        # Download to buffer
                        buffer = BytesIO()
                        stream.stream_to_buffer(buffer)
                        buffer.seek(0)
                        
                        # Send file
                        if download_type == "audio":
                            context.bot.send_audio(
                                chat_id=query.message.chat_id,
                                audio=buffer,
                                caption=f"🔊 {yt.title} ({i}/{total_videos})",
                                timeout=300
                            )
                        else:
                            context.bot.send_video(
                                chat_id=query.message.chat_id,
                                video=buffer,
                                caption=f"🎥 {yt.title} ({i}/{total_videos})",
                                timeout=300
                            )
                        
                        success_count += 1
                    except Exception as e:
                        context.bot.send_message(
                            chat_id=query.message.chat_id,
                            text=f"❌ Failed to download video {i}: {str(e)}"
                        )
                        logger.error(f"Playlist item error: {e}")
                
                # Send completion message
                context.bot.send_message(
                    chat_id=query.message.chat_id,
                    text=f"✅ Playlist download complete!\n\nSuccessfully downloaded {success_count}/{total_videos} videos."
                )
                
                # Delete progress message
                context.bot.delete_message(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id
                )
            except Exception as e:
                context.bot.edit_message_text(
                    chat_id=query.message.chat_id,
                    message_id=query.message.message_id,
                    text=f"❌ Error downloading playlist: {str(e)}"
                )
                logger.error(f"Playlist download error: {e}")
        
        Thread(target=download_playlist).start()
    except Exception as e:
        query.edit_message_text(f"❌ Error: {str(e)}")
        logger.error(f"Playlist init error: {e}")

def handle_url(update: Update, context: CallbackContext):
    """Handle direct YouTube URLs"""
    user_id = update.effective_user.id
    if not is_subscribed(user_id):
        update.message.reply_text("Please subscribe to use this feature.")
        return
    
    url = update.message.text
    if "youtube.com" not in url and "youtu.be" not in url:
        update.message.reply_text("Please provide a valid YouTube URL.")
        return
    
    try:
        if "list=" in url:
            playlist(update, context)
            return
        
        yt = YouTube(url)
        
        # Get available streams
        streams = yt.streams.filter(progressive=True, file_extension='mp4').order_by('resolution').desc()
        audio_streams = yt.streams.filter(only_audio=True, file_extension='mp4').order_by('abr').desc()
        
        # Create quality buttons
        keyboard = []
        
        # Video options
        video_options = []
        for stream in streams:
            if stream.resolution not in [s.resolution for s in video_options]:
                video_options.append(stream)
        
        for stream in video_options:
            keyboard.append([
                InlineKeyboardButton(
                    f"🎥 {stream.resolution} ({stream.mime_type.split('/')[1]})",
                    callback_data=f"download_video_{yt.video_id}_{stream.itag}"
                )
            ])
        
        # Audio options
        audio_options = []
        for stream in audio_streams:
            if stream.abr not in [s.abr for s in audio_options]:
                audio_options.append(stream)
        
        for stream in audio_options:
            keyboard.append([
                InlineKeyboardButton(
                    f"🔊 Audio ({stream.abr})",
                    callback_data=f"download_audio_{yt.video_id}_{stream.itag}"
                )
            ])
        
        reply_markup = InlineKeyboardMarkup(keyboard)
        update.message.reply_text(
            f"Select download option for:\n\n"
            f"📹 {yt.title}\n"
            f"⏱ {format_duration(yt.length)}\n"
            f"👁 {yt.views:,} views",
            rep