import csv
import logging
import os
import psutil
import pyautogui
import random
import signal
import subprocess
import threading
import time
import atexit
import requests
from datetime import datetime, timedelta

global ONGOING_MEETING
global VIDEO_PANEL_HIDED
global TELEGRAM_TOKEN
global TELEGRAM_RETRIES
global TELEGRAM_CHAT_ID

logging.basicConfig(
    format='%(asctime)s %(levelname)s %(message)s', level=logging.INFO)


def get_env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def get_env_int(name, default, min_value=None, max_value=None):
    raw_value = os.getenv(name)
    if raw_value is None or raw_value.strip() == "":
        return default

    try:
        value = int(raw_value.strip())
    except ValueError:
        logging.warning("Invalid integer for %s=%r. Falling back to %s.", name, raw_value, default)
        return default

    if min_value is not None and value < min_value:
        logging.warning("%s=%s below min %s. Falling back to %s.", name, value, min_value, default)
        return default
    if max_value is not None and value > max_value:
        logging.warning("%s=%s above max %s. Falling back to %s.", name, value, max_value, default)
        return default
    return value


def get_env_choice(name, default, allowed_values):
    value = os.getenv(name, default).strip().lower()
    if value in allowed_values:
        return value
    logging.warning("Invalid value for %s=%r. Allowed: %s. Falling back to %s.",
                    name, value, ",".join(sorted(allowed_values)), default)
    return default

# Turn DEBUG on:
#   - screenshot on error
#   - record joining
#   - do not exit container on error
DEBUG = get_env_bool('DEBUG', False)

# Disable failsafe
pyautogui.FAILSAFE = False

# Get vars
BASE_PATH = os.getenv('HOME')
CSV_PATH = os.path.join(BASE_PATH, "meetings.csv")
IMG_PATH = os.path.join(BASE_PATH, "img")
REC_PATH = os.path.join(BASE_PATH, "recordings")
AUDIO_PATH = os.path.join(BASE_PATH, "audio")
DEBUG_PATH = os.path.join(REC_PATH, "screenshots")

TELEGRAM_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')
TELEGRAM_RETRIES = 5

DISPLAY_NAME = os.getenv('DISPLAY_NAME')
if DISPLAY_NAME is None or  len(DISPLAY_NAME) < 3:
    NAME_LIST = [
        'iPhone',
        'iPad',
        'Macbook',
        'Desktop',
        'Huawei',
        'Mobile',
        'PC',
        'Windows',
        'Home',
        'MyPC',
        'Computer',
        'Android'
    ]
    DISPLAY_NAME = random.choice(NAME_LIST)

TIME_FORMAT = "%Y-%m-%d_%H-%M-%S"
CSV_DELIMITER = ';'
MEETING_END_BUFFER_SECONDS = 300
SCHEDULER_POLL_INTERVAL_SECONDS = 30
MAX_JOIN_AUDIO_ATTEMPTS = 3
RECORD_CONTAINER_FORMAT = get_env_choice('RECORD_CONTAINER_FORMAT', 'mkv', {'mkv', 'mp4'})
ENABLE_SEGMENTED_RECORDING = get_env_bool('ENABLE_SEGMENTED_RECORDING', False)
SEGMENT_MINUTES = get_env_int('SEGMENT_MINUTES', 15, min_value=1, max_value=240)
REMUX_TO_MP4 = get_env_bool('REMUX_TO_MP4', False)
DELETE_SOURCE_AFTER_REMUX = get_env_bool('DELETE_SOURCE_AFTER_REMUX', False)
VIDEO_CODEC = os.getenv('VIDEO_CODEC', 'libx264').strip() or 'libx264'
VIDEO_CRF = get_env_int('VIDEO_CRF', 28, min_value=0, max_value=51)
VIDEO_PRESET = os.getenv('VIDEO_PRESET', 'veryfast').strip()
if not VIDEO_PRESET:
    VIDEO_PRESET = 'veryfast'
VIDEO_FPS = get_env_int('VIDEO_FPS', 15, min_value=1, max_value=60)
AUDIO_CODEC = get_env_choice('AUDIO_CODEC', 'aac', {'aac', 'libmp3lame', 'opus'})
AUDIO_BITRATE = os.getenv('AUDIO_BITRATE', '128k').strip()
if not AUDIO_BITRATE:
    AUDIO_BITRATE = '128k'
MAX_FFMPEG_RESTARTS = get_env_int('MAX_FFMPEG_RESTARTS', 5, min_value=0, max_value=20)
RELOAD_TRIGGER_FILE = os.path.join(BASE_PATH, ".meetings.reload")

ONGOING_MEETING = False
VIDEO_PANEL_HIDED = False
STARTED_MEETINGS = set()
RELOAD_REQUESTED = False


class BackgroundThread:

    def __init__(self, interval=10):
        # Sleep interval between
        self.interval = interval

        thread = threading.Thread(target=self.run, args=())
        thread.daemon = True  # Daemonize thread
        thread.start()  # Start the execution

    def run(self):
        global ONGOING_MEETING
        ONGOING_MEETING = True

        logging.debug("Check continuously if meeting has ended..")

        while ONGOING_MEETING:

            # Check if recording
            if (pyautogui.locateCenterOnScreen(os.path.join(IMG_PATH, 'meeting_is_being_recorded.png'), confidence=0.9,
                                               minSearchTime=2) is not None):
                logging.info("This meeting is being recorded..")
                try:
                    x, y = pyautogui.locateCenterOnScreen(os.path.join(
                        IMG_PATH, 'got_it.png'), confidence=0.9)
                    pyautogui.click(x, y)
                    logging.info("Accepted recording..")
                except TypeError:
                    logging.error("Could not accept recording!")

            # Check if ended
            if (pyautogui.locateOnScreen(os.path.join(IMG_PATH, 'meeting_ended_by_host_1.png'),
                                         confidence=0.9) is not None or pyautogui.locateOnScreen(
                os.path.join(IMG_PATH, 'meeting_ended_by_host_2.png'), confidence=0.9) is not None):
                ONGOING_MEETING = False
                logging.info("Meeting ended by host..")
            time.sleep(self.interval)


class HideViewOptionsThread:

    def __init__(self, description, interval=10):
        # Sleep interval between
        self.interval = interval
        self.description = description

        thread = threading.Thread(target=self.run, args=())
        thread.daemon = True  # Daemonize thread
        thread.start()  # Start the execution

    def run(self):
        global VIDEO_PANEL_HIDED
        logging.debug("Check continuously if screensharing is active..")
        while ONGOING_MEETING:
            # Check if host is sharing poll results
            if (pyautogui.locateCenterOnScreen(os.path.join(IMG_PATH, 'host_is_sharing_poll_results.png'),
                                               confidence=0.9,
                                               minSearchTime=2) is not None):
                logging.info("Host is sharing poll results..")
                try:
                    x, y = pyautogui.locateCenterOnScreen(os.path.join(
                        IMG_PATH, 'host_is_sharing_poll_results.png'), confidence=0.9)
                    pyautogui.click(x, y)
                    try:
                        x, y = pyautogui.locateCenterOnScreen(os.path.join(
                            IMG_PATH, 'exit.png'), confidence=0.9)
                        pyautogui.click(x, y)
                        logging.info("Closed poll results window..")
                    except TypeError:
                        logging.error("Could not exit poll results window!")
                        if DEBUG:
                            pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                                TIME_FORMAT) + "-" + self.description) + "_close_poll_results_error.png")
                except TypeError:
                    logging.error("Could not find poll results window anymore!")
                    if DEBUG:
                        pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                            TIME_FORMAT) + "-" + self.description) + "_find_poll_results_error.png")

            # Check if view options available
            if pyautogui.locateOnScreen(os.path.join(IMG_PATH, 'view_options.png'), confidence=0.9) is not None:
                if not VIDEO_PANEL_HIDED:
                    logging.info("Screensharing active..")
                    try:
                        x, y = pyautogui.locateCenterOnScreen(os.path.join(
                            IMG_PATH, 'view_options.png'), confidence=0.9)
                        pyautogui.click(x, y)
                        time.sleep(1)
                        # Hide video panel
                        if pyautogui.locateOnScreen(os.path.join(IMG_PATH, 'show_video_panel.png'),
                                                    confidence=0.9) is not None:
                            # Leave 'Show video panel' and move mouse from screen
                            pyautogui.moveTo(0, 100)
                            pyautogui.click(0, 100)
                            VIDEO_PANEL_HIDED = True
                        else:
                            try:
                                x, y = pyautogui.locateCenterOnScreen(os.path.join(
                                    IMG_PATH, 'hide_video_panel.png'), confidence=0.9)
                                pyautogui.click(x, y)
                                # Move mouse from screen
                                pyautogui.moveTo(0, 100)
                                VIDEO_PANEL_HIDED = True
                            except TypeError:
                                logging.error("Could not hide video panel!")
                    except TypeError:
                        logging.error("Could not find view options!")
            else:
                VIDEO_PANEL_HIDED = False

            time.sleep(self.interval)

def send_telegram_message(text):
    global TELEGRAM_TOKEN
    global TELEGRAM_CHAT_ID
    global TELEGRAM_RETRIES
	
    if TELEGRAM_TOKEN is None:
        logging.error("Telegram token is missing. No Telegram messages will be send!")
        return
    
    if TELEGRAM_CHAT_ID is None:
        logging.error("Telegram chat_id is missing. No Telegram messages will be send!")
        return
        
    if len(TELEGRAM_TOKEN) < 3 or len(TELEGRAM_CHAT_ID) < 3:
        logging.error("Telegram token or chat_id missing. No Telegram messages will be send!")
        return

    url_req = "https://api.telegram.org/bot" + TELEGRAM_TOKEN + "/sendMessage" + "?chat_id=" + TELEGRAM_CHAT_ID + "&text=" + text 
    tries = 0
    done = False
    while not done:
        results = requests.get(url_req)
        results = results.json()
        done = 'ok' in results and results['ok']
        tries+=1
        if not done and tries < TELEGRAM_RETRIES:
            logging.error("Sending Telegram message failed, retring in 5 seconds...")
            time.sleep(5)
        if not done and tries >= TELEGRAM_RETRIES:
            logging.error("Sending Telegram message failed {} times, please check your credentials!".format(tries))
            done = True


def unregister_killpg_handler():
    try:
        atexit.unregister(os.killpg)
    except Exception:
        pass


def wait_for_display_ready(display_name, retries=8, delay=2):
    for _ in range(retries):
        check = subprocess.run(
            ["xdpyinfo", "-display", display_name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if check.returncode == 0:
            return True
        time.sleep(delay)
    return False


def stop_process_group(process, process_name, timeout_seconds=8):
    if process is None:
        return
    try:
        if process.poll() is not None:
            return
    except Exception:
        return

    try:
        os.killpg(os.getpgid(process.pid), signal.SIGINT)
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if process.poll() is not None:
                return
            time.sleep(0.2)
    except Exception:
        pass

    try:
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGTERM)
            time.sleep(1)
    except Exception:
        pass

    try:
        if process.poll() is None:
            os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except Exception as ex:
        logging.warning("Could not force stop %s: %s", process_name, ex)


def get_audio_codec_args():
    if AUDIO_CODEC == 'libmp3lame':
        return ["-acodec", "libmp3lame", "-ar", "44100", "-aq", "2"]
    if AUDIO_CODEC == 'opus':
        return ["-acodec", "libopus", "-ar", "48000", "-b:a", AUDIO_BITRATE]
    return ["-acodec", "aac", "-ar", "44100", "-b:a", AUDIO_BITRATE]


def remux_to_mp4(input_path):
    output_path = os.path.splitext(input_path)[0] + ".mp4"
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-c",
        "copy",
        "-movflags",
        "+faststart",
        output_path,
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode == 0:
        logging.info("Remuxed recording to MP4: %s", output_path)
        if DELETE_SOURCE_AFTER_REMUX:
            try:
                os.remove(input_path)
                logging.info("Deleted source file after remux: %s", input_path)
            except OSError as ex:
                logging.warning("Failed deleting source after remux (%s): %s", input_path, ex)
        return output_path

    logging.error("MP4 remux failed for %s: %s", input_path, result.stderr.decode(errors='ignore'))
    return None


def request_reload(_signum, _frame):
    global RELOAD_REQUESTED
    RELOAD_REQUESTED = True
    logging.info("Received reload signal. meetings.csv will be reloaded.")


def should_reload_now():
    global RELOAD_REQUESTED
    if RELOAD_REQUESTED:
        RELOAD_REQUESTED = False
        return True

    if os.path.exists(RELOAD_TRIGGER_FILE):
        try:
            os.remove(RELOAD_TRIGGER_FILE)
        except OSError:
            pass
        logging.info("Reload trigger file detected. meetings.csv will be reloaded.")
        return True
    return False


def check_connecting(zoom_pid, start_date, duration):
    # Check if connecting
    check_periods = 0
    connecting = False
    # Check if connecting
    if pyautogui.locateCenterOnScreen(os.path.join(IMG_PATH, 'connecting.png'), confidence=0.9) is not None:
        connecting = True
        logging.info("Connecting..")

    # Wait while connecting
    # Exit when meeting ends after time
    while connecting:
        if (datetime.now() - start_date).total_seconds() > duration:
            logging.info("Meeting ended after time!")
            logging.info("Exit Zoom!")
            os.killpg(os.getpgid(zoom_pid), signal.SIGQUIT)
            return

        if pyautogui.locateCenterOnScreen(os.path.join(IMG_PATH, 'connecting.png'), confidence=0.9) is None:
            logging.info("Maybe not connecting anymore..")
            check_periods += 1
            if check_periods >= 2:
                connecting = False
                logging.info("Not connecting anymore..")
                return
        time.sleep(2)


def join_meeting_id(meet_id):
    logging.info("Join a meeting by ID..")
    found_join_meeting = False
    try:
        x, y = pyautogui.locateCenterOnScreen(os.path.join(
            IMG_PATH, 'join_meeting.png'), minSearchTime=2, confidence=0.9)
        pyautogui.click(x, y)
        found_join_meeting = True
    except TypeError:
        pass

    if not found_join_meeting:
        logging.error("Could not find 'Join Meeting' on screen!")
        return False

    time.sleep(2)

    # Insert meeting id
    pyautogui.press('tab')
    pyautogui.press('tab')
    pyautogui.write(meet_id, interval=0.1)

    # Insert name
    pyautogui.press('tab')
    pyautogui.press('tab')
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.write(DISPLAY_NAME, interval=0.1)

    # Configure
    pyautogui.press('tab')
    pyautogui.press('space')
    pyautogui.press('tab')
    pyautogui.press('tab')
    pyautogui.press('space')
    pyautogui.press('tab')
    pyautogui.press('tab')
    pyautogui.press('space')

    time.sleep(2)

    return check_error()


def join_meeting_url():
    logging.info("Join a meeting by URL..")

    # Insert name
    pyautogui.hotkey('ctrl', 'a')
    pyautogui.write(DISPLAY_NAME, interval=0.1)

    # Configure
    pyautogui.press('tab')
    pyautogui.press('space')
    pyautogui.press('tab')
    pyautogui.press('space')
    pyautogui.press('tab')
    pyautogui.press('space')

    time.sleep(2)

    return check_error()
    

def check_error():
    # Sometimes invalid id error is displayed
    if pyautogui.locateCenterOnScreen(os.path.join(
            IMG_PATH, 'invalid_meeting_id.png'), confidence=0.9) is not None:
        logging.error("Maybe a invalid meeting id was inserted..")
        left = False
        try:
            x, y = pyautogui.locateCenterOnScreen(
                os.path.join(IMG_PATH, 'leave.png'), confidence=0.9)
            pyautogui.click(x, y)
            left = True
        except TypeError:
            pass
            # Valid id

        if left:
            if pyautogui.locateCenterOnScreen(os.path.join(
                    IMG_PATH, 'join_meeting.png'), confidence=0.9) is not None:
                logging.error("Invalid meeting id!")
                return False
        else:
            return True

    if pyautogui.locateCenterOnScreen(os.path.join(
            IMG_PATH, 'authorized_attendees_only.png'), confidence=0.9) is not None:
        logging.error("This meeting is for authorized attendees only!")
        return False

    return True


def find_process_id_by_name(process_name):
    list_of_process_objects = []
    # Iterate over the all the running process
    for proc in psutil.process_iter():
        try:
            pinfo = proc.as_dict(attrs=['pid', 'name'])
            # Check if process name contains the given name string.
            if process_name.lower() in pinfo['name'].lower():
                list_of_process_objects.append(pinfo)
        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
            pass
    return list_of_process_objects


def show_toolbars():
    # Mouse move to show toolbar
    width, height = pyautogui.size()
    y = (height / 2)
    pyautogui.moveTo(0, y, duration=0.5)
    pyautogui.moveTo(width - 1, y, duration=0.5)


def join_audio(description, max_attempts=MAX_JOIN_AUDIO_ATTEMPTS):
    for attempt in range(1, max_attempts + 1):
        try:
            x, y = pyautogui.locateCenterOnScreen(os.path.join(
                IMG_PATH, 'join_with_computer_audio.png'), confidence=0.9)
            logging.info("Join with computer audio..")
            pyautogui.click(x, y)
            return True
        except TypeError:
            logging.warning("Could not join with computer audio (attempt %s/%s).",
                            attempt, max_attempts)

        time.sleep(1)
        try:
            show_toolbars()
            x, y = pyautogui.locateCenterOnScreen(os.path.join(
                IMG_PATH, 'join_audio.png'), confidence=0.9)
            pyautogui.click(x, y)
        except TypeError:
            logging.warning("Could not find generic join audio button (attempt %s/%s).",
                            attempt, max_attempts)

        time.sleep(1)

    logging.error("Could not join audio after %s attempts.", max_attempts)
    if DEBUG:
        pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
            TIME_FORMAT) + "-" + description) + "_join_audio_error.png")
    return False


def unmute(description):
    try:
        show_toolbars()
        x, y = pyautogui.locateCenterOnScreen(os.path.join(
            IMG_PATH, 'unmute.png'), confidence=0.9)
        pyautogui.click(x, y)
        return True
    except TypeError:
        logging.error("Could not unmute!")
        if DEBUG:
            pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(TIME_FORMAT) + "-" + description) + "_unmute_error.png")
        return False


def mute(description):
    try:
        show_toolbars()
        x, y = pyautogui.locateCenterOnScreen(os.path.join(
            IMG_PATH, 'mute.png'), confidence=0.9)
        pyautogui.click(x, y)
        return True
    except TypeError:
        logging.error("Could not mute!")
        if DEBUG:
            pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(TIME_FORMAT) + "-" + description) + "_mute_error.png")
        return False


def join(meet_id, meet_pw, duration, description):
    global VIDEO_PANEL_HIDED
    ffmpeg_debug = None
    ffmpeg_log = None
    ffmpeg = None
    recording_files = []
    segment_patterns = []

    # HEADLESS STABILIZATION
    # Wait for environment/VNC to be fully ready before any interaction
    logging.info("Stabilizing headless environment (10s delay)..")
    time.sleep(10)

    # Automatically hide the terminal window (titled 'zoomrec' in entrypoint.sh) 
    # so it doesn't appear in the recording even if on top
    try:
        subprocess.run("wmctrl -r \"zoomrec\" -b add,hidden", shell=True, check=False)
        logging.info("Headless: Minimized terminal window using wmctrl.")
    except Exception:
        pass

    logging.info("Join meeting: " + description)

    if DEBUG:
        # Start recording
        width, height = pyautogui.size()
        resolution = str(width) + 'x' + str(height)
        disp = os.getenv('DISPLAY')

        logging.info("Start recording..")

        filename = os.path.join(
            REC_PATH, time.strftime(TIME_FORMAT)) + "-" + description + "-JOIN.mkv"

        command = "ffmpeg -nostats -loglevel quiet -f pulse -ac 2 -i 1 -f x11grab -r 30 -s " + resolution + " -i " + \
                  disp + " -acodec pcm_s16le -vcodec libx264rgb -preset ultrafast -crf 0 -threads 0 -async 1 -vsync 1 " + filename

        ffmpeg_debug = subprocess.Popen(
            command, stdout=subprocess.PIPE, shell=True, preexec_fn=os.setsid)
        atexit.register(os.killpg, os.getpgid(
            ffmpeg_debug.pid), signal.SIGQUIT)

    # Exit Zoom or Firefox if running
    exit_process_by_name("zoom")
    exit_process_by_name("firefox")

    join_by_url = meet_id.startswith('https://') or meet_id.startswith('http://')
    process_name = 'firefox' if join_by_url else 'zoom'

    if not join_by_url:
        # Start Zoom
        zoom = subprocess.Popen("zoom", stdout=subprocess.PIPE,
                                shell=True, preexec_fn=os.setsid)
        img_name = 'join_meeting.png'
    else:
        logging.info("Starting firefox with url")
        zoom = subprocess.Popen(f'firefox "{meet_id}"', stdout=subprocess.PIPE,
                                shell=True, preexec_fn=os.setsid)
        img_name = None # Skip app-specific image wait for firefox
    
    # Wait while process is there
    list_of_process_ids = find_process_id_by_name(process_name)
    while len(list_of_process_ids) <= 0:
        logging.info(f"No Running {process_name} Process found!")
        list_of_process_ids = find_process_id_by_name(process_name)
        time.sleep(1)

    # Wait for app to be started
    if img_name:
        while pyautogui.locateCenterOnScreen(os.path.join(IMG_PATH, img_name), confidence=0.9) is None:
            logging.info(f"{process_name} not ready yet!")
            time.sleep(1)
    else:
        # Give firefox some time to start
        time.sleep(5)

    logging.info(f"{process_name} started!")
    start_date = datetime.now()

    if not join_by_url:
        joined = join_meeting_id(meet_id)
        if not joined:
            send_telegram_message("Failed to join meeting {}!".format(description))
            logging.error("Failed to join meeting!")
            os.killpg(os.getpgid(zoom.pid), signal.SIGQUIT)
            if DEBUG and ffmpeg_debug is not None:
                # closing ffmpeg
                os.killpg(os.getpgid(ffmpeg_debug.pid), signal.SIGQUIT)
                unregister_killpg_handler()
            return

        # Check if connecting
        check_connecting(zoom.pid, start_date, duration)

        pyautogui.write(meet_pw, interval=0.2)
        pyautogui.press('tab')
        pyautogui.press('space')

        # Joined meeting
        # Check if connecting
        check_connecting(zoom.pid, start_date, duration)

        # Check if meeting is started by host
        check_periods = 0
        meeting_started = True

        time.sleep(2)

        # Check if waiting for host
        if pyautogui.locateCenterOnScreen(os.path.join(
                IMG_PATH, 'wait_for_host.png'), confidence=0.9, minSearchTime=3) is not None:
            meeting_started = False
            logging.info("Please wait for the host to start this meeting.")

        # Wait for the host to start this meeting
        # Exit when meeting ends after time
        while not meeting_started:
            if (datetime.now() - start_date).total_seconds() > duration:
                logging.info("Meeting ended after time!")
                logging.info(f"Exit {process_name}!")
                os.killpg(os.getpgid(zoom.pid), signal.SIGQUIT)
                if DEBUG:
                    os.killpg(os.getpgid(ffmpeg_debug.pid), signal.SIGQUIT)
                    unregister_killpg_handler()
                return

            if pyautogui.locateCenterOnScreen(os.path.join(
                    IMG_PATH, 'wait_for_host.png'), confidence=0.9) is None:
                logging.info("Maybe meeting was started now.")
                check_periods += 1
                if check_periods >= 2:
                    meeting_started = True
                    logging.info("Meeting started by host.")
                    break
            time.sleep(2)

        # Check if connecting
        check_connecting(zoom.pid, start_date, duration)

        # Check if in waiting room
        check_periods = 0
        in_waitingroom = False

        time.sleep(2)

        # Check if joined into waiting room
        if pyautogui.locateCenterOnScreen(os.path.join(IMG_PATH, 'waiting_room.png'), confidence=0.9,
                                          minSearchTime=3) is not None:
            in_waitingroom = True
            logging.info("Please wait, the meeting host will let you in soon..")

        # Wait while host will let you in
        # Exit when meeting ends after time
        while in_waitingroom:
            if (datetime.now() - start_date).total_seconds() > duration:
                logging.info("Meeting ended after time!")
                logging.info(f"Exit {process_name}!")
                os.killpg(os.getpgid(zoom.pid), signal.SIGQUIT)
                if DEBUG:
                    os.killpg(os.getpgid(ffmpeg_debug.pid), signal.SIGQUIT)
                    unregister_killpg_handler()
                return

            if pyautogui.locateCenterOnScreen(os.path.join(
                    IMG_PATH, 'waiting_room.png'), confidence=0.9) is None:
                logging.info("Maybe no longer in the waiting room..")
                check_periods += 1
                if check_periods == 2:
                    logging.info("No longer in the waiting room..")
                    break
            time.sleep(2)

        # Meeting joined
        # Check if connecting
        check_connecting(zoom.pid, start_date, duration)

    else:
        # Firefox flow - simplified for now as per user request
        logging.info("Firefox join - assuming URL navigation is sufficient for now")
        joined = True
        # Start a thread to check if browser is still running intermittently if needed, 
        # but the main loop at the end will handle duration.
        pass

    # Set ONGOING_MEETING to True explicitly to avoid race condition with main loop
    global ONGOING_MEETING
    ONGOING_MEETING = True
    
    print("\n" + "="*40)
    print("STATUS: JOINED MEETING")
    print("DURATION: %d seconds" % duration)
    print("="*40 + "\n")
    
    logging.info("Joined meeting: %s" % description)
    logging.info("Requested duration: %d seconds" % duration)
    
    # Check if recording warning is shown at the beginning (Zoom app only)
    if not join_by_url:
        if (pyautogui.locateCenterOnScreen(os.path.join(IMG_PATH, 'meeting_is_being_recorded.png'), confidence=0.9,
                                           minSearchTime=2) is not None):
            logging.info("This meeting is being recorded..")
            try:
                x, y = pyautogui.locateCenterOnScreen(os.path.join(
                    IMG_PATH, 'got_it.png'), confidence=0.9)
                pyautogui.click(x, y)
                logging.info("Accepted recording..")
            except TypeError:
                logging.error("Could not accept recording!")

        # Check if host is sharing poll results at the beginning
        if (pyautogui.locateCenterOnScreen(os.path.join(IMG_PATH, 'host_is_sharing_poll_results.png'), confidence=0.9,
                                           minSearchTime=2) is not None):
            logging.info("Host is sharing poll results..")
            try:
                x, y = pyautogui.locateCenterOnScreen(os.path.join(
                    IMG_PATH, 'host_is_sharing_poll_results.png'), confidence=0.9)
                pyautogui.click(x, y)
                try:
                    x, y = pyautogui.locateCenterOnScreen(os.path.join(
                        IMG_PATH, 'exit.png'), confidence=0.9)
                    pyautogui.click(x, y)
                    logging.info("Closed poll results window..")
                except TypeError:
                    logging.error("Could not exit poll results window!")
                    if DEBUG:
                        pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                            TIME_FORMAT) + "-" + description) + "_close_poll_results_error.png")
            except TypeError:
                logging.error("Could not find poll results window anymore!")
                if DEBUG:
                    pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                        TIME_FORMAT) + "-" + description) + "_find_poll_results_error.png")

    # Start BackgroundThread only for Zoom app joins to avoid false positives in Firefox
    if not join_by_url:
        BackgroundThread()

    if not join_by_url:
        # Set computer audio
        time.sleep(2)
        if not join_audio(description):
            logging.info(f"Exit {process_name}!")
            os.killpg(os.getpgid(zoom.pid), signal.SIGQUIT)
            if DEBUG:
                os.killpg(os.getpgid(ffmpeg_debug.pid), signal.SIGQUIT)
                unregister_killpg_handler()
            send_telegram_message("Failed to join audio in meeting '{}'.".format(description))
            return

        # 'Say' something if path available (mounted)
        if os.path.exists(AUDIO_PATH):
            play_audio(description)

        time.sleep(2)
        logging.info("Enter fullscreen..")
        show_toolbars()
        try:
            x, y = pyautogui.locateCenterOnScreen(
                os.path.join(IMG_PATH, 'view.png'), confidence=0.9)
            pyautogui.click(x, y)
        except TypeError:
            logging.error("Could not find view!")
            if DEBUG:
                pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                    TIME_FORMAT) + "-" + description) + "_view_error.png")

        time.sleep(2)

        fullscreen = False
        try:
            x, y = pyautogui.locateCenterOnScreen(
                os.path.join(IMG_PATH, 'fullscreen.png'), confidence=0.9)
            pyautogui.click(x, y)
            fullscreen = True
        except TypeError:
            logging.error("Could not find fullscreen!")
            if DEBUG:
                pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                    TIME_FORMAT) + "-" + description) + "_fullscreen_error.png")

        # TODO: Check for 'Exit Full Screen': already fullscreen -> fullscreen = True

        # Screensharing already active
        if not fullscreen:
            try:
                x, y = pyautogui.locateCenterOnScreen(os.path.join(
                    IMG_PATH, 'view_options.png'), confidence=0.9)
                pyautogui.click(x, y)
            except TypeError:
                logging.error("Could not find view options!")
                if DEBUG:
                    pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                        TIME_FORMAT) + "-" + description) + "_view_options_error.png")

            # Switch to fullscreen
            time.sleep(2)
            show_toolbars()

            logging.info("Enter fullscreen..")
            try:
                x, y = pyautogui.locateCenterOnScreen(os.path.join(
                    IMG_PATH, 'enter_fullscreen.png'), confidence=0.9)
                pyautogui.click(x, y)
            except TypeError:
                logging.error("Could not enter fullscreen by image!")
                if DEBUG:
                    pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                        TIME_FORMAT) + "-" + description) + "_enter_fullscreen_error.png")
                return

            time.sleep(2)

        # Screensharing not active
        screensharing_active = False
        try:
            x, y = pyautogui.locateCenterOnScreen(os.path.join(
                IMG_PATH, 'view_options.png'), confidence=0.9)
            pyautogui.click(x, y)
            screensharing_active = True
        except TypeError:
            logging.error("Could not find view options!")
            if DEBUG:
                pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                    TIME_FORMAT) + "-" + description) + "_view_options_error.png")

        time.sleep(2)

        if screensharing_active:
            # hide video panel
            try:
                x, y = pyautogui.locateCenterOnScreen(os.path.join(
                    IMG_PATH, 'hide_video_panel.png'), confidence=0.9)
                pyautogui.click(x, y)
                VIDEO_PANEL_HIDED = True
            except TypeError:
                logging.error("Could not hide video panel!")
                if DEBUG:
                    pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                        TIME_FORMAT) + "-" + description) + "_hide_video_panel_error.png")
        else:
            # switch to speaker view
            show_toolbars()

            logging.info("Switch view..")
            try:
                x, y = pyautogui.locateCenterOnScreen(
                    os.path.join(IMG_PATH, 'view.png'), confidence=0.9)
                pyautogui.click(x, y)
            except TypeError:
                logging.error("Could not find view!")
                if DEBUG:
                    pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                        TIME_FORMAT) + "-" + description) + "_view_error.png")

            time.sleep(2)

            try:
                # speaker view
                x, y = pyautogui.locateCenterOnScreen(os.path.join(
                    IMG_PATH, 'speaker_view.png'), confidence=0.9)
                pyautogui.click(x, y)
            except TypeError:
                logging.error("Could not switch speaker view!")
                if DEBUG:
                    pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                        TIME_FORMAT) + "-" + description) + "_speaker_view_error.png")

            try:
                # minimize panel
                x, y = pyautogui.locateCenterOnScreen(os.path.join(
                    IMG_PATH, 'minimize.png'), confidence=0.9)
                pyautogui.click(x, y)
            except TypeError:
                logging.error("Could not minimize panel!")
                if DEBUG:
                    pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                        TIME_FORMAT) + "-" + description) + "_minimize_error.png")

        # Move mouse from screen
        pyautogui.moveTo(0, 100)
        pyautogui.click(0, 100)
    else:
        # Center mouse and click to ensure focus on Firefox
        width_px, height_px = pyautogui.size()
        pyautogui.moveTo(width_px // 2, height_px // 2)
        pyautogui.click()
        time.sleep(1)
        
        # Try to maximize Firefox using F11 (Fullscreen Mode)
        logging.info("Attempting to maximize Firefox (F11 Fullscreen)..")
        pyautogui.press('f11')
        time.sleep(3) # Give it time to enter fullscreen

        # SECONDARY: Force maximize Firefox using wmctrl in case F11 fails or loses focus
        try:
            subprocess.run("wmctrl -r \"Mozilla Firefox\" -b add,maximized_vert,maximized_horz", shell=True, check=False)
            logging.info("Headless: Forced Firefox maximization with wmctrl.")
        except Exception:
            pass

    if DEBUG and ffmpeg_debug is not None:
        stop_process_group(ffmpeg_debug, "ffmpeg_debug")
        unregister_killpg_handler()

    # Audio
    # Start recording
    logging.info("Start recording..")

    base_filename_no_ext = os.path.join(REC_PATH, time.strftime(
        TIME_FORMAT) + "-" + description)

    # DYNAMIC RESOLUTION DETECTION (STABLE)
    try:
        xdpy_output = subprocess.check_output("xdpyinfo | grep dimensions", shell=True).decode()
        # format: "  dimensions:    1920x1040 pixels (507x273 millimeters)"
        resolution_str_raw = xdpy_output.split()[1] # grab 1920x1040
        width, height = map(int, resolution_str_raw.split('x'))
        logging.info(f"OS Reports Resolution: {width}x{height}")
    except Exception as e:
        logging.warning(f"Could not get resolution from xdpyinfo: {e}. Falling back to pyautogui.")
        width, height = pyautogui.size()
        
    # FORCE DIMENSIONS TO BE DIVISIBLE BY 2 (REQUIRED BY LIBX264)
    width = (width // 2) * 2
    height = (height // 2) * 2
    resolution_str = f"{width}x{height}"

    disp = os.getenv('DISPLAY') if os.getenv('DISPLAY') else ":1"
    
    print("\n" + "="*40)
    print(f"SCREEN RESOLUTION: {resolution_str}")
    print(f"DISPLAY: {disp}")
    print("="*40 + "\n")
    logging.info(f"Screen resolution: {resolution_str} | Capturing whole display: {disp}")

    if not wait_for_display_ready(disp):
        logging.error("Display %s is not ready for capture.", disp)
        send_telegram_message("Display {} is not available for recording '{}'.".format(disp, description))
        try:
            os.killpg(os.getpgid(zoom.pid), signal.SIGQUIT)
        except Exception:
            pass
        return

    ffmpeg_cmd_base = [
        "ffmpeg",
        "-nostats",
        "-loglevel",
        "error",
        "-f",
        "pulse",
        "-ac",
        "2",
        "-thread_queue_size",
        "512",
        "-i",
        "1",
        "-f",
        "x11grab",
        "-framerate",
        str(VIDEO_FPS),
        "-video_size",
        resolution_str,
        "-use_wallclock_as_timestamps",
        "1",
        "-i",
        disp,
        "-vcodec",
        VIDEO_CODEC,
        "-crf",
        str(VIDEO_CRF),
        "-preset",
        VIDEO_PRESET,
        "-pix_fmt",
        "yuv420p",
    ]
    ffmpeg_cmd_base.extend(get_audio_codec_args())
    ffmpeg_cmd_base.extend(["-threads", "0", "-async", "1"])

    ffmpeg_log_path = os.path.join(REC_PATH, "ffmpeg.log")
    ffmpeg_log = open(ffmpeg_log_path, "ab")
    def start_recording_process(restart_idx):
        cmd = list(ffmpeg_cmd_base)
        if ENABLE_SEGMENTED_RECORDING:
            segment_seconds = max(60, SEGMENT_MINUTES * 60)
            segment_prefix = f"{base_filename_no_ext}-part{restart_idx:02d}"
            output_pattern = f"{segment_prefix}-%Y%m%d_%H%M%S.{RECORD_CONTAINER_FORMAT}"
            cmd.extend([
                "-f",
                "segment",
                "-segment_time",
                str(segment_seconds),
                "-reset_timestamps",
                "1",
                "-strftime",
                "1",
                output_pattern,
            ])
            process = subprocess.Popen(
                cmd, stdout=ffmpeg_log, stderr=ffmpeg_log, preexec_fn=os.setsid)
            return process, None, output_pattern

        output_file = f"{base_filename_no_ext}.{RECORD_CONTAINER_FORMAT}" if restart_idx == 0 else \
            f"{base_filename_no_ext}-cont-{restart_idx:02d}.{RECORD_CONTAINER_FORMAT}"
        cmd.append(output_file)
        process = subprocess.Popen(
            cmd, stdout=ffmpeg_log, stderr=ffmpeg_log, preexec_fn=os.setsid)
        return process, output_file, None

    ffmpeg, recording_file, segment_pattern = start_recording_process(0)
    if recording_file is not None:
        recording_files.append(recording_file)
    if segment_pattern is not None:
        segment_patterns.append(segment_pattern)

    # Check if ffmpeg died immediately or has initial errors
    time.sleep(2)
    if ffmpeg.poll() is not None:
        logging.error(f"FFmpeg failed to start! Check log file: {ffmpeg_log_path}")
        print("\n" + "!"*40)
        print("CRITICAL ERROR: FFmpeg failed to start!")
        print(f"Check ffmpeg log file: {ffmpeg_log_path}")
        print("!"*40 + "\n")
    else:
        logging.info("FFmpeg started successfully.")
        try:
            atexit.register(os.killpg, os.getpgid(
                ffmpeg.pid), signal.SIGQUIT)
        except ProcessLookupError:
            logging.error("FFmpeg process disappeared before atexit registration!")

    start_date = datetime.now()
    end_date = start_date + timedelta(seconds=duration + MEETING_END_BUFFER_SECONDS)

    # Start thread to check active screensharing only for Zoom app
    if not join_by_url:
        HideViewOptionsThread(description=description)

    # Send Telegram Notification
    send_telegram_message("Joined Meeting '{}' and started recording.".format(description))
    
    print("\n" + "*"*40)
    print("RECORDING STARTED")
    print(f"Scheduled end time: {end_date.strftime('%H:%M:%S')}")
    print("*"*40 + "\n")
    logging.info("Recording started! Scheduled to end at: %s" % end_date.strftime('%H:%M:%S'))
    
    ffmpeg_restart_attempts = 0
    meeting_running = True
    while meeting_running:
        if ffmpeg.poll() is not None:
            logging.error("FFmpeg exited unexpectedly with return code %s.", ffmpeg.returncode)
            ffmpeg_restart_attempts += 1
            if ffmpeg_restart_attempts > MAX_FFMPEG_RESTARTS:
                send_telegram_message(
                    "Recording for meeting '{}' stopped unexpectedly after {} ffmpeg restarts.".format(
                        description, MAX_FFMPEG_RESTARTS))
                ONGOING_MEETING = False
                break

            if not wait_for_display_ready(disp, retries=5, delay=2):
                logging.error("Display %s unavailable while trying to restart ffmpeg.", disp)
                time.sleep(2)
                continue

            ffmpeg, recording_file, segment_pattern = start_recording_process(ffmpeg_restart_attempts)
            time.sleep(2)
            if ffmpeg.poll() is not None:
                logging.error("FFmpeg restart attempt %s failed.", ffmpeg_restart_attempts)
                continue

            if recording_file is not None:
                recording_files.append(recording_file)
            if segment_pattern is not None:
                segment_patterns.append(segment_pattern)

            logging.warning("FFmpeg restarted successfully (attempt %s/%s).",
                            ffmpeg_restart_attempts, MAX_FFMPEG_RESTARTS)
            send_telegram_message(
                "Recording capture restarted for meeting '{}' (attempt {}/{}).".format(
                    description, ffmpeg_restart_attempts, MAX_FFMPEG_RESTARTS))
            continue

        time_now = datetime.now()
        time_remaining = end_date - time_now
        
        total_sec_remaining = int(time_remaining.total_seconds())
        
        if total_sec_remaining <= 0:
            meeting_running = False
            print("\nRecording duration reached.")
        elif not ONGOING_MEETING:
            meeting_running = False
            print("\nMeeting ended (ONGOING_MEETING flag reset).")
        else:
            # Format time remaining as HH:MM:SS
            hours, remainder = divmod(total_sec_remaining, 3600)
            minutes, seconds = divmod(remainder, 60)
            countdown = f"{hours:02}:{minutes:02}:{seconds:02}"
            print(f"STATUS: Recording | Ends in {countdown} (at {end_date.strftime('%H:%M:%S')})", end="\r", flush=True)
            
        time.sleep(5)

    print("\n" + "="*40)
    print("RECORDING STOPPED")
    print("Stop time: %s" % datetime.now().strftime('%H:%M:%S'))
    print("="*40 + "\n")
    logging.info("Recording stopped at %s" % datetime.now().strftime('%H:%M:%S'))

    # Close everything
    if DEBUG and ffmpeg_debug is not None:
        try:
            os.killpg(os.getpgid(ffmpeg_debug.pid), signal.SIGQUIT)
        except (ProcessLookupError, AttributeError):
            pass
        unregister_killpg_handler()

    try:
        os.killpg(os.getpgid(zoom.pid), signal.SIGQUIT)
    except (ProcessLookupError, AttributeError):
        pass

    try:
        if ffmpeg.poll() is None:
            stop_process_group(ffmpeg, "ffmpeg_main")
    except (ProcessLookupError, AttributeError, NameError):
        pass
    if ffmpeg_log is not None and not ffmpeg_log.closed:
        ffmpeg_log.close()
    unregister_killpg_handler()

    if REMUX_TO_MP4 and not ENABLE_SEGMENTED_RECORDING:
        for recording_file in recording_files:
            if recording_file is not None and os.path.exists(recording_file):
                remux_to_mp4(recording_file)

    if not ONGOING_MEETING:
        try:
            # Press OK after meeting ended by host
            x, y = pyautogui.locateCenterOnScreen(
                os.path.join(IMG_PATH, 'ok.png'), confidence=0.9)
            pyautogui.click(x, y)
        except TypeError:
            if DEBUG:
                pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                    TIME_FORMAT) + "-" + description) + "_ok_error.png")
                
    send_telegram_message("Meeting '{}' ended.".format(description))

def play_audio(description):
    # Get all files in audio directory
    files=os.listdir(AUDIO_PATH)
    # Filter .wav files
    files=list(filter(lambda f: f.endswith(".wav"), files))
    # Check if .wav files available
    if len(files) > 0:
        unmute(description)
        # Get random file
        file=random.choice(files)
        path = os.path.join(AUDIO_PATH, file)
        # Use paplay to play .wav file on specific Output
        command = "/usr/bin/paplay --device=microphone -p " + path
        play = subprocess.Popen(command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        res, err = play.communicate()
        if play.returncode != 0:
            logging.error("Failed playing file! - " + str(play.returncode) + " - " + str(err))
        else:
            logging.debug("Successfully played audio file! - " + str(play.returncode))
        mute(description)
    else:
        logging.error("No .wav files found!")


def exit_process_by_name(name):
    list_of_process_ids = find_process_id_by_name(name)
    if len(list_of_process_ids) > 0:
        logging.info(name + " process exists | killing..")
        for elem in list_of_process_ids:
            process_id = elem['pid']
            try:
                os.kill(process_id, signal.SIGKILL)
            except Exception as ex:
                logging.error("Could not terminate " + name +
                              "[" + str(process_id) + "]: " + str(ex))


def parse_record_flag(value):
    return str(value).strip().lower() in {'true', '1', 'yes', 'y'}


def parse_meetings():
    meetings = []
    if not os.path.exists(CSV_PATH):
        logging.error("CSV file missing: %s", CSV_PATH)
        return meetings

    with open(CSV_PATH, mode='r') as csv_file:
        csv_reader = csv.DictReader(csv_file, delimiter=CSV_DELIMITER)
        for row_number, row in enumerate(csv_reader, start=2):
            description = (row.get("description") or f"meeting_{row_number}").strip() or f"meeting_{row_number}"
            if not parse_record_flag(row.get("record", "false")):
                continue

            try:
                meeting_date = datetime.strptime(row["date"], '%Y-%m-%d').date()
                meeting_time = datetime.strptime(row["time"], '%H:%M').time()
                duration_minutes = int(row["duration"])
            except (TypeError, ValueError, KeyError):
                logging.error("Invalid CSV values at line %s (%s). Skipping row.", row_number, description)
                continue

            if duration_minutes <= 0:
                logging.error("Invalid duration at line %s (%s). Duration must be > 0.", row_number, description)
                continue

            meeting_id = (row.get("id") or "").strip()
            if not meeting_id:
                logging.error("Missing meeting id at line %s (%s).", row_number, description)
                continue

            meetings.append({
                "date": meeting_date,
                "time": meeting_time,
                "duration_minutes": duration_minutes,
                "id": meeting_id,
                "password": (row.get("password") or "").strip(),
                "description": description,
            })

    return meetings


def get_meeting_bounds(meeting):
    start_date = datetime.combine(meeting["date"], meeting["time"])
    planned_end = start_date + timedelta(minutes=meeting["duration_minutes"])
    buffered_end = planned_end + timedelta(seconds=MEETING_END_BUFFER_SECONDS)
    return start_date, planned_end, buffered_end


def get_meeting_key(meeting):
    return "{}|{}|{}|{}".format(
        meeting["date"].isoformat(),
        meeting["time"].strftime('%H:%M'),
        meeting["id"],
        meeting["description"],
    )


def get_next_meeting_start(now, meetings):
    next_start = None
    for meeting in meetings:
        start_date, _, _ = get_meeting_bounds(meeting)
        if start_date > now and (next_start is None or start_date < next_start):
            next_start = start_date
    return next_start


def find_due_meeting(now, meetings):
    for meeting in meetings:
        start_date, planned_end, buffered_end = get_meeting_bounds(meeting)
        meeting_key = get_meeting_key(meeting)

        if meeting_key in STARTED_MEETINGS:
            continue
        if now < start_date or now > buffered_end:
            continue

        remaining_duration = max(1, int((planned_end - now).total_seconds()))
        return meeting, meeting_key, remaining_duration
    return None


def run_scheduler_loop():
    while True:
        if should_reload_now():
            logging.info("Reload request acknowledged.")

        now = datetime.now()
        meetings = parse_meetings()
        due_meeting = find_due_meeting(now, meetings)

        if due_meeting is not None:
            meeting, meeting_key, remaining_duration = due_meeting
            STARTED_MEETINGS.add(meeting_key)
            logging.info("Joining scheduled meeting: %s", meeting["description"])
            join(meet_id=meeting["id"], meet_pw=meeting["password"],
                 duration=remaining_duration, description=meeting["description"])
            continue

        next_start = get_next_meeting_start(now, meetings)
        if next_start is None:
            print("No upcoming meetings found.", end="\r", flush=True)
        else:
            print(f"Next meeting in {next_start - now}", end="\r", flush=True)
        slept = 0
        while slept < SCHEDULER_POLL_INTERVAL_SECONDS:
            if should_reload_now():
                logging.info("Reloading meetings.csv immediately.")
                break
            time.sleep(1)
            slept += 1


def main():
    try:
        if DEBUG and not os.path.exists(DEBUG_PATH):
            os.makedirs(DEBUG_PATH)
    except Exception:
        logging.error("Failed to create screenshot folder!")
        raise

    try:
        signal.signal(signal.SIGHUP, request_reload)
    except Exception:
        pass

    logging.info("Starting date-aware scheduler (poll interval: %ss).", SCHEDULER_POLL_INTERVAL_SECONDS)
    run_scheduler_loop()


if __name__ == '__main__':
    main()
