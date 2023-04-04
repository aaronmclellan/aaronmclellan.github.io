import requests
import datetime
import pytz

def get_next_f1_race():
    url = "https://ergast.com/api/f1/current/next.json"
    response = requests.get(url)
    data = response.json()

    next_race = data['MRData']['RaceTable']['Races'][0]
    race_location = next_race['Circuit']['Location']['locality'] + ", " + next_race['Circuit']['Location']['country']
    race_time_utc = datetime.datetime.fromisoformat(next_race['date'] + 'T' + next_race['time'][:-1])

    return race_location, race_time_utc

def convert_utc_to_cst(utc_time):
    utc = pytz.utc
    cst = pytz.timezone('America/Chicago')
    cst_time = utc_time.replace(tzinfo=utc).astimezone(cst)
    
    return cst_time

if __name__ == "__main__":
    location, time_utc = get_next_f1_race()
    time_cst = convert_utc_to_cst(time_utc)
    
    print(f"Next F1 race location: {location}")
    print(f"Next F1 race time (CST): {time_cst.strftime('%Y-%m-%d %H:%M:%S')}")
