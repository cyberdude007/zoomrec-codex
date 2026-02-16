import csv
import logging
import os
import psutil
import pyautogui
import random
import schedule
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

# Turn DEBUG on:
#   - screenshot on error
#   - record joining
#   - do not exit container on error
DEBUG = True if os.getenv('DEBUG') == 'True' else False

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

ONGOING_MEETING = False
VIDEO_PANEL_HIDED = False


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

    def __init__(self, interval=10):
        # Sleep interval between
        self.interval = interval

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
                                TIME_FORMAT) + "-" + description) + "_close_poll_results_error.png")
                except TypeError:
                    logging.error("Could not find poll results window anymore!")
                    if DEBUG:
                        pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                            TIME_FORMAT) + "-" + description) + "_find_poll_results_error.png")

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


def join_audio(description):
    audio_joined = False
    try:
        x, y = pyautogui.locateCenterOnScreen(os.path.join(
            IMG_PATH, 'join_with_computer_audio.png'), confidence=0.9)
        logging.info("Join with computer audio..")
        pyautogui.click(x, y)
        audio_joined = True
        return True
    except TypeError:
        logging.error("Could not join with computer audio!")
        if DEBUG:
            pyautogui.screenshot(os.path.join(DEBUG_PATH, time.strftime(
                TIME_FORMAT) + "-" + description) + "_join_with_computer_audio_error.png")
    time.sleep(1)
    if not audio_joined:
        try:
            show_toolbars()
            x, y = pyautogui.locateCenterOnScreen(os.path.join(
                IMG_PATH, 'join_audio.png'), confidence=0.9)
            pyautogui.click(x, y)
            join_audio(description)
        except TypeError:
            logging.error("Could not join audio!")
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
                atexit.unregister(os.killpg)
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
                    atexit.unregister(os.killpg)
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
                    atexit.unregister(os.killpg)
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
                atexit.unregister(os.killpg)
            time.sleep(2)
            join(meet_id, meet_pw, duration, description)

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
        os.killpg(os.getpgid(ffmpeg_debug.pid), signal.SIGQUIT)
        atexit.unregister(os.killpg)

    # Audio
    # Start recording
    logging.info("Start recording..")

    filename = os.path.join(REC_PATH, time.strftime(
        TIME_FORMAT) + "-" + description) + ".mkv"

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

    # Aggressively optimized for small file size (targeting ~1.5-2MB/min)
    # 1. libmp3lame (compressed audio) reduces size by ~9MB/min over pcm_s16le
    # 2. r 15 (reduced framerate) is sufficient for screen sharing and reduces video size
    # 3. crf 28 (increased compression) keeps text readable but saves space
    command = f"ffmpeg -stats -loglevel error -f pulse -ac 2 -i 1 -f x11grab -r 15 -video_size {resolution_str} -i {disp} " \
              "-vcodec libx264 -crf 28 -preset veryfast -pix_fmt yuv420p -acodec libmp3lame -ar 44100 -aq 2 -threads 0 -async 1 -vsync 1 " + filename

    ffmpeg = subprocess.Popen(
        command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, shell=True, preexec_fn=os.setsid)

    # Check if ffmpeg died immediately or has initial errors
    time.sleep(2)
    if ffmpeg.poll() is not None:
        error_output = ffmpeg.stderr.read().decode()
        logging.error(f"FFmpeg failed to start! Error: {error_output}")
        print("\n" + "!"*40)
        print("CRITICAL ERROR: FFmpeg failed to start!")
        print(error_output)
        print("!"*40 + "\n")
    else:
        logging.info("FFmpeg started successfully.")
        try:
            atexit.register(os.killpg, os.getpgid(
                ffmpeg.pid), signal.SIGQUIT)
        except ProcessLookupError:
            logging.error("FFmpeg process disappeared before atexit registration!")

    start_date = datetime.now()
    end_date = start_date + timedelta(seconds=duration + 300)  # Add 5 minutes buffer

    # Start thread to check active screensharing only for Zoom app
    if not join_by_url:
        HideViewOptionsThread()

    # Send Telegram Notification
    send_telegram_message("Joined Meeting '{}' and started recording.".format(description))
    
    print("\n" + "*"*40)
    print("RECORDING STARTED")
    print(f"Scheduled end time: {end_date.strftime('%H:%M:%S')}")
    print("*"*40 + "\n")
    logging.info("Recording started! Scheduled to end at: %s" % end_date.strftime('%H:%M:%S'))
    
    meeting_running = True
    while meeting_running:
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
        atexit.unregister(os.killpg)

    try:
        os.killpg(os.getpgid(zoom.pid), signal.SIGQUIT)
    except (ProcessLookupError, AttributeError):
        pass

    try:
        if ffmpeg.poll() is None:
            os.killpg(os.getpgid(ffmpeg.pid), signal.SIGQUIT)
    except (ProcessLookupError, AttributeError, NameError):
        pass
    atexit.unregister(os.killpg)

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


def join_ongoing_meeting():
    with open(CSV_PATH, mode='r') as csv_file:
        csv_reader = csv.DictReader(csv_file, delimiter=CSV_DELIMITER)
        for row in csv_reader:
            # Check and join ongoing meeting
            curr_date = datetime.now()

            # Date format: YYYY-MM-DD
            try:
                meeting_date = datetime.strptime(row["date"], '%Y-%m-%d').date()
            except (ValueError, KeyError):
                logging.error("Invalid date format in CSV for row: %s" % row.get('description', 'unknown'))
                continue

            if meeting_date == curr_date.date():
                curr_time = curr_date.time()

                start_time_csv = datetime.strptime(row["time"], '%H:%M')
                start_date = curr_date.replace(
                    hour=start_time_csv.hour, minute=start_time_csv.minute, second=0, microsecond=0)
                start_time = start_date.time()

                end_date = start_date + \
                    timedelta(seconds=int(row["duration"]) * 60 + 300)  # Add 5 minutes
                end_time = end_date.time()

                recent_duration = (end_date - curr_date).total_seconds()

                if start_time < end_time:
                    if curr_time >= start_time and curr_time <= end_time and str(row["record"]) == 'true':
                            logging.info(
                                "Join meeting that is currently running or scheduled for today..")
                            join(meet_id=row["id"], meet_pw=row["password"],
                                 duration=recent_duration, description=row["description"])
                else:  # crosses midnight
                    if (curr_time >= start_time or curr_time <= end_time) and str(row["record"]) == 'true':
                            logging.info(
                                "Join meeting that is currently running or scheduled for today..")
                            join(meet_id=row["id"], meet_pw=row["password"],
                                 duration=recent_duration, description=row["description"])


def setup_schedule():
    with open(CSV_PATH, mode='r') as csv_file:
        csv_reader = csv.DictReader(csv_file, delimiter=CSV_DELIMITER)
        line_count = 0
        for row in csv_reader:
            if str(row["record"]) == 'true':
                try:
                    meeting_date = datetime.strptime(row["date"], '%Y-%m-%d').date()
                except (ValueError, KeyError):
                    logging.error("Invalid date format in CSV for row: %s" % row.get('description', 'unknown'))
                    continue
                
                # Only schedule for today (ongoing joins handle immediate, this handles future today)
                if meeting_date == datetime.now().date():
                    start_time_obj = datetime.strptime(row["time"], '%H:%M')
                    # Schedule 1 minute before
                    run_time = (start_time_obj - timedelta(minutes=1)).strftime('%H:%M')
                    
                    cmd_string = f"schedule.every().day.at(\"{run_time}\").do(join, meet_id=\"{row['id']}\", " \
                                 f"meet_pw=\"{row['password']}\", duration={int(row['duration']) * 60}, " \
                                 f"description=\"{row['description']}\")"

                    cmd = compile(cmd_string, "<string>", "eval")
                    eval(cmd)
                    line_count += 1
        logging.info("Added %s meetings to today's schedule." % line_count)


def main():
    try:
        if DEBUG and not os.path.exists(DEBUG_PATH):
            os.makedirs(DEBUG_PATH)
    except Exception:
        logging.error("Failed to create screenshot folder!")
        raise

    setup_schedule()
    join_ongoing_meeting()


if __name__ == '__main__':
    main()

while True:
    schedule.run_pending()
    time.sleep(1)
    time_of_next_run = schedule.next_run()
    time_now = datetime.now()
    remaining = time_of_next_run - time_now
    print(f"Next meeting in {remaining}", end="\r", flush=True)
