import csv
import datetime
import logging
import xml.etree.ElementTree as ETree
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

BASE_URL = "https://www.missevan.com"


def get_drama_sound_lists(drama_id):
    url = f"{BASE_URL}/dramaapi/getdrama?drama_id={drama_id}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        data = response.json().get("info", {})
        drama = data.get('drama', {})
        episodes = data.get("episodes", {}).get("episode", [])

        sound_lists = [{
            "sound_id": episode["sound_id"],
            "sound_title": episode["soundstr"],
            'need_pay': episode.get("need_pay", 0)
        } for episode in episodes]

        return sound_lists, drama.get('name'), drama.get('price'), drama.get('view_count'), drama.get('catalog_name')
    except requests.RequestException as e:
        logging.error(f"Error fetching sound lists for drama ID {drama_id}: {e}")
        return [], '', '', '', ''


def get_sound_detail(sound_id):
    url = f"{BASE_URL}/sound/getsound?soundid={sound_id}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        sound = response.json().get("info", {}).get("sound", {})

        return {
            "sound_id": sound_id,
            "view_count": sound.get("view_count"),
            "view_count_formatted": sound.get("view_count_formatted"),
            "comment_count": sound.get("comment_count"),
            "favorite_count": sound.get("favorite_count"),
            "username": sound.get("username"),
            "create_time": datetime.datetime.fromtimestamp(sound.get('create_time', 0)),
        }
    except requests.RequestException as e:
        logging.error(f"Error fetching sound detail for sound ID {sound_id}: {e}")
        return {}


def fetch_all_danmakus(sound_id):
    url = f"{BASE_URL}/sound/getdm?soundid={sound_id}"
    try:
        response = requests.get(url)
        response.raise_for_status()
        pp_comments_xml = ETree.fromstring(response.text)
        return {int(item.attrib["p"].split(",")[6]) for item in pp_comments_xml.findall("d") if
                item.attrib["p"].split(",")[1] != "4"}
    except (requests.RequestException, ETree.ParseError) as e:
        logging.error(f"Error fetching popup comments for sound ID {sound_id}: {e}")
        return set()


def extract_user_ids(data):
    user_ids = {int(comment["userid"]) for comment in data["info"]["comment"]["Datas"]}
    user_ids.update(
        int(sub["userid"]) for comment in data["info"]["comment"]["Datas"] for sub in comment["subcomments"])
    return user_ids


def fetch_all_uids_by_comments(sound_id):
    endpoint = f"{BASE_URL}/site/getcomment?type=1&e_id={sound_id}&order=3&p={{}}&pagesize=100"
    comments_uids = set()
    page = 1

    while True:
        response = requests.get(endpoint.format(page))
        response.raise_for_status()
        data = response.json()
        comments_uids.update(extract_user_ids(data))

        if not data["info"]["comment"]["hasMore"]:
            break
        page += 1

    return comments_uids


def get_user_input():
    return input("Enter the drama ids (separate with commas, e.g, 62452,68690,72732,74464,74005,68204,74309): ")


def process_sound(sound):
    sound_id = sound.get('sound_id')
    sound_detail = get_sound_detail(sound_id)
    danmaku_uids = fetch_all_danmakus(sound_id)
    comment_uids = fetch_all_uids_by_comments(sound_id)

    sound_detail.update({
        'sound_id': sound_id,
        'sound_title': sound.get('sound_title'),
        'need_pay': sound.get('need_pay'),
        'danmaku_uids': danmaku_uids,
        'comment_uids': comment_uids,
        'total_sound_uids': danmaku_uids.union(comment_uids),
    })

    return sound_detail


def process_drama_id(drama_id, sound_writer, drama_writer):
    logging.info(f"Processing drama: (ID: {drama_id})")
    sound_lists, name, price, view_count, catalog_name = get_drama_sound_lists(drama_id)
    sound_data = []
    total_paid_udis = set()
    total_free_udis = set()

    total_paid_danmaku_udis = set()
    total_paid_comment_uids = set()
    total_free_danmaku_udis = set()
    total_free_comment_uids = set()

    paid_view_count = 0
    free_view_count = 0
    first_sound_create_time = None

    if sound_lists:
        with ThreadPoolExecutor() as executor:
            future_to_sound = {executor.submit(process_sound, sound): sound for sound in sound_lists}
            for future in as_completed(future_to_sound):
                sound_detail = future.result()
                if first_sound_create_time is None or sound_detail['create_time'] < first_sound_create_time:
                    first_sound_create_time = sound_detail['create_time']
                if sound_detail:
                    if future_to_sound[future].get('need_pay') > 0:
                        total_paid_udis.update(sound_detail['total_sound_uids'])
                        paid_view_count += int(sound_detail['view_count'])

                        total_paid_danmaku_udis.update(sound_detail['danmaku_uids'])
                        total_paid_comment_uids.update(sound_detail['comment_uids'])
                    else:
                        total_free_udis.update(sound_detail['total_sound_uids'])
                        total_free_danmaku_udis.update(sound_detail['danmaku_uids'])
                        total_free_comment_uids.update(sound_detail['comment_uids'])
                        free_view_count += int(sound_detail['view_count'])

                    sound_data.append(sound_detail)

    # Order sound_data by sound_id
    sound_data.sort(key=lambda x: x['sound_id'])

    for sound_detail in sound_data:
        sound_writer.writerow([
            sound_detail['sound_title'], sound_detail['create_time'], sound_detail['need_pay'],
            len(sound_detail['danmaku_uids']), len(sound_detail['comment_uids']),
            len(sound_detail['total_sound_uids'])
        ])

    drama_writer.writerow([
        drama_id, name, first_sound_create_time, price, view_count, paid_view_count, free_view_count,
        len(total_paid_danmaku_udis), len(total_paid_comment_uids), len(total_free_danmaku_udis),
        len(total_free_comment_uids), len(total_paid_udis), len(total_free_udis)
    ])

    return sound_data, total_paid_udis


def runner():
    drama_ids = get_user_input()
    drama_sound = {}
    all_paid_total_uids = set()

    with open('sound_data.csv', mode='w', newline='', encoding='utf-8') as sound_file, \
            open('drama_data.csv', mode='w', newline='', encoding='utf-8') as drama_file:
        sound_writer = csv.writer(sound_file)
        drama_writer = csv.writer(drama_file)

        sound_writer.writerow(["声音标题", "创建时间", "是否需要付费", "弹幕用户ID", "评论用户ID", "总用户ID"])
        drama_writer.writerow(
            ["剧集ID", "剧集名称", "首个声音创建时间", "价格", "总观看次数", "付费观看次数", "免费观看次数",
             "付费弹幕用户ID", "付费评论用户ID", "免费弹幕用户ID", "免费评论用户ID",
             "付费总用户ID", "免费总用户ID"])

        for drama_id in drama_ids.split(','):
            sound_data, total_paid_udis = process_drama_id(drama_id.strip(), sound_writer, drama_writer)
            drama_sound[drama_id] = sound_data
            all_paid_total_uids.update(total_paid_udis)

    print('-------------------------------------------------')
    print(f"All Paid Total UIDs: {len(all_paid_total_uids)}")
    print('-------------------------------------------------')
    return drama_sound, all_paid_total_uids


if __name__ == '__main__':
    runner()
