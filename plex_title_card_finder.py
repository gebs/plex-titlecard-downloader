from inspect import getargs
import json, re, os, glob, argparse,time
import requests
import praw
from pydrive.auth import GoogleAuth, ServiceAccountCredentials
from pydrive.drive import GoogleDrive
import schedule
import time
from logs import MyLogger
from datetime import datetime

SEARCH_MODE = 'strict'
ASSET_ROOT = '/assets'

total_shows = 0
total_missing_ep = 0
total_missing_shows = 0
total_downloaded = 0

drive = None

def get_args(env_str,default, arg_bool=False,arg_int=False):
    env_var = os.environ.get(env_str)
    if env_var:
        if arg_bool:
            if env_var is True or env_var is False:
                return env_var
            elif env_var.lower() in ["t","true"]:
                return True
            else:
                return False
        elif arg_int:
            return int(env_var)
        else:
            return str(env_var)
    else:
        return default

def load_excluded_links():
    source_string = './exclude.json'
    if bool(os.path.isfile(source_string)):
        with open(source_string,'r') as f:
            src = f.read()

        return json.loads(src)
    else:
        return ""

parser = argparse.ArgumentParser()
parser.add_argument("-r", "--run",dest="run",help="Run without the scheduler",action="store_true",default=False)
args = parser.parse_args()

sonarr_apikey = get_args("SONARR_APIKEY",None)
sonarr_url = get_args("SONARR_URL",None)
reddit_clientId =get_args("REDDIT_CLIENTID",None)
reddit_clientSecret=get_args("REDDIT_CLIENTSECRET",None)
ntfy_server = get_args("NTFY_SERVER",None)
ntfy_user = get_args("NTFY_USER",None)

logger = MyLogger("Plex Title Cards", "./", 100, "-", True, True)

EXCLUDED_RESULTS = load_excluded_links()


def saveGoogleDriveFiles(link,missing_episodes,series_path):
    logger.info("Search Google Drive for missing files")
    global drive
    global total_downloaded
    if (drive is None):
        gauth = GoogleAuth()
        gauth.auth_method = 'service'
        gauth.credentials = ServiceAccountCredentials.from_json_keyfile_name("/credentials/credentials.json", ['https://www.googleapis.com/auth/drive'])
        drive = GoogleDrive(gauth)

    folder_id = link.split('/')[-1]

    folder_queue = [folder_id]

    missing = []
    seasons = []
    for i in missing_episodes:
        missing.append(missing_episodes[i]['se'])
        seasons.append(missing_episodes[i]['season'])

    d = 0
    while len(folder_queue) != 0:
        current_folder_id = folder_queue.pop(0)
        file_list = drive.ListFile({'q': "'{}' in parents and trashed=false".format(current_folder_id)}).GetList()

        for file1 in file_list:
            if file1['mimeType'] == 'application/vnd.google-apps.folder':
                season = re.search(r"[1-9]{0,1}[0-9]{1}$",file1['title'])
                if season and season.group() in seasons:
                    folder_queue.append(file1['id'])
            else:
                regex = re.search(r"S\d{1,2}E\d{1,2}",file1['title'])
                if regex:
                   current_SE = regex.group()
                   if current_SE in missing:
                       full_name = series_path + '/' + current_SE + '.' + file1['fileExtension']
                       logger.info('Downloading File: ' + full_name)
                       file1.GetContentFile(full_name)
                       total_downloaded = total_downloaded + 1
                       d += 1
                       write_downloaded_episodes(current_SE,full_name)
    if d == 0:
        logger.info("Missing title cards not found")
    elif d < len(missing):
        logger.info("There are still title cards missing") 
    

# Function to convert  
def listToString(s): 
    
    # initialize an empty string
    str1 = " AND " 
    
    # return string  
    return (str1.join(s))

def generate_search_string(series_name):

    if SEARCH_MODE == 'strict':
        search_string = '"' + series_name + '"'
    else:
        search_string = series_name

    return search_string


def process_season(series_name):
    global total_missing_shows
    logger.info("scanning r/PlexTitleCards...")

    write_title = False
    y = 0

    reddit = praw.Reddit(
    client_id=reddit_clientId,
    client_secret=reddit_clientSecret,
    redirect_uri="http://localhost:8080",
    user_agent="Plex Title Card Matcher",
    )

    reddit.read_only = True

    generate_search_string(series_name)

    for submission in reddit.subreddit("PlexTitleCards").search(generate_search_string(series_name), limit=None, syntax="lucene"):

        if (series_name in EXCLUDED_RESULTS) and (submission.permalink in EXCLUDED_RESULTS[series_name]):
            continue

        author = submission.author.name
        flair = submission.link_flair_text
        if flair is not None and bool(re.search('request|discussion',str.lower(''.join(map(str, flair))))):
            pass

        else:

            if not is_fullpack(submission.title):
                pass
            else:
                total_missing_shows = total_missing_shows + 1
                logger.info("Found a post on /r/plextitlecards")
                if not write_title:
                    with open("/output/Output_Plex_TitleCards.txt", "a", encoding="utf-8") as text_file:
                        text_file.write("\n### Results Found For: %s" % series_name + " ###\n")
                        write_title = True

                with open("/output/Output_Plex_TitleCards.txt", "a", encoding="utf-8") as text_file:
                    text_file.write(submission.title + "\n")
                    text_file.write("     " + "https://www.reddit.com" + submission.permalink + "\n")
                    text_file.write("     " + author + "\n")
                
                y = y+1

    if y == 0:
        logger.info("no results found")
    
    logger.info("")



def is_fullpack(submission_name):
    """Audits the submission name to determine if it's a single episode or a full pack"""
    return not bool(re.search('(s\d{1,4}e\d{1,4})+',str.lower(submission_name)))

def asset_exists(series_path):
    """Check if the asset folder already has assets for this series"""
    validation_path = ASSET_ROOT + series_path[series_path.rfind('/'):].replace('/','/')

    for files in os.walk(validation_path):
        return ( bool(re.search('(s\d{1,4}e\d{1,4})+', str.lower(''.join(map(str, files))) )) or ('source.txt' in str.lower(''.join(map(str, files)))) )

def missing_episode_assets(series_id, series_name, series_path):
    """compare assets with expected episdoes"""
    global total_missing_ep


    logger.info("Local assets found...")

    validation_path = ASSET_ROOT + series_path[series_path.rfind('/'):].replace('/','/')
    logger.info("scanning path... " + validation_path)

    response_episode = requests.get(f'{sonarr_url}/api/episode?seriesID={series_id}&apikey={sonarr_apikey}')
    json_episodes = json.loads(response_episode.text)

    e = 0
    missing_episodes = dict()
    cnt = 0
    for element in json_episodes:
        season = element['seasonNumber']
        episode = element['episodeNumber']
        hasfile = element['hasFile']

        if season > 0 and hasfile:
            search_string = 'S' + str(season).zfill(2) + 'E' + str(episode).zfill(2)

            f = glob.glob(validation_path+'/'+search_string+'.*')

            if len(f) == 0:
                asset_missing = True
            else:
                for g in f:
                    if g.lower().endswith(('.png', '.jpg', '.jpeg')):
                        asset_missing = False

            if asset_missing:
                total_missing_ep = total_missing_ep + 1
                missing_episodes[cnt] = dict()
                missing_episodes[cnt]['series_name'] = series_name
                missing_episodes[cnt]['season'] = str(season)
                missing_episodes[cnt]['episode'] = str(episode)
                missing_episodes[cnt]['se'] = search_string
                cnt = cnt + 1

    if missing_episodes:           
        write_missing_episodes_header(series_name,validation_path)
        source_link = get_source_txt(validation_path)
        if len(source_link) > 0 and 'https://drive.google.com/drive/folders/' in source_link:
            saveGoogleDriveFiles(source_link,missing_episodes,validation_path)

        write_missing_episodes(missing_episodes)
    else:
        logger.info("No missing titlecards")
    logger.info("")

def write_missing_episodes_header(series_name,validation_path):
    with open("/output/Output_Plex_TitleCards_Missing.txt", "a", encoding="utf-8") as text_file:
         text_file.write("\n" + '### Missing Files For: ' + series_name + ' ###' + "\n")
         text_file.write("\n" + get_source_txt(validation_path) + "\n")
def write_downloaded_episodes(se,path):
    with open("/output/Output_Plex_TitleCards_Missing.txt", "a", encoding="utf-8") as text_file:
        text_file.write(se)
        text_file.write(" was missing but downloaded from google drive: " + path + "\n")
def write_missing_episodes(missing_episodes):
    with open("/output/Output_Plex_TitleCards_Missing.txt", "a", encoding="utf-8") as text_file:
        for i in missing_episodes:        
            text_file.write(missing_episodes[i]['se'])
            text_file.write(" is missing" + "\n")

def get_source_txt(validation_path):
    """get contents of a text file to append to assets_missing test file"""

    source_string = validation_path + '/source.txt'
    if bool(os.path.isfile(source_string)):
        with open(source_string,'r') as f:
            src = f.read()

        return src
    else:
        return ""

def send_notification(text):
    if ntfy_server:
        requests.post(ntfy_server,
        data=json.dumps({
            "topic": "plex-titlecards-downloader",
            "message": text,
            "title": "Plex Titlecards downloader finished",
        }),
        headers={"Authorization": ntfy_user}
        )

def scan():
    """Kick off the primary process."""
    logger.add_main_handler()
    logger.separator("Started Title Cards search at " + datetime.now().strftime("%H:%M:%S"))
    logger.info("")

    z = 0

    with open("/output/Output_Plex_TitleCards.txt", "w", encoding="utf-8") as text_file:
      text_file.write("Output for for today...\n")

    
    with open("/output/Output_Plex_TitleCards_Missing.txt", "w", encoding="utf-8") as text_file:
        text_file.write("Output for for today...\n")

    response_series = requests.get(f'{sonarr_url}/api/series?apikey={sonarr_apikey}')
    json_series = json.loads(response_series.text)

    for element in json_series:
        series_id = element['id']
        series_name = element['title']
        series_path = element['path']

        if series_name == "3%":
            continue
        
        logger.separator("Processing " + series_name)

        if asset_exists(series_path):
            missing_episode_assets(series_id, series_name, series_path)
        else:
            process_season(series_name)

    logger.info("")
    logger.separator("Results")

    logger.info("")
    logger.info("Total shows scanned: " + str(z))
    logger.info("Total new shows found: " + str(total_missing_shows))
    logger.info("Total missing episodes: " + str(total_missing_ep - total_downloaded))
    logger.info("Total cards downloaded: " + str(total_downloaded))
    logger.info("")
    logger.separator()

    send_notification("Total shows scanned: "+ str(z)
                    + "\nTotal new shows found: " + str(total_missing_shows)
                    + "\nTotal missing episodes: " + str(total_missing_ep - total_downloaded)
                    + "\nTotal cards downloaded: " + str(total_downloaded))



def main():
    if args.run:
        scan()
    else:
        schedule.every().day.at("05:30").do(scan)
        schedule.every().day.at("11:30").do(scan)
        schedule.every().day.at("15:30").do(scan)
        schedule.every().day.at("23:30").do(scan)

        while True:
            schedule.run_pending()
            time.sleep(10)
    
if __name__ == "__main__":
    main()