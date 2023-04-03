import requests
from bs4 import BeautifulSoup
import pytz
import datetime

# URL of the ESPN Formula 1 schedule page
url = "https://www.espn.com/f1/schedule"

# Send a request to the website and get the content
response = requests.get(url)
content = response.content

# Parse the content using Beautiful Soup
soup = BeautifulSoup(content, "html.parser")
print("SOUP")
print(soup)
test = soup.find("table", class_="Table__TR")
print("TEST")
print(test)
# Find the next race in the schedule table
table = soup.find("table")
print("TABLE")
print(table)
rows = table.find_all("tr")
for row in rows:
    cells = row.find_all("td")
    if len(cells) > 0 and cells[0].text.strip() == "Next Race":
        location = cells[1].text.strip()
        date_time = cells[2].text.strip()
        break

# Convert the race time to CST timezone
race_time = datetime.datetime.strptime(date_time, "%a, %b %d, %Y - %I:%M %p %Z")
utc = pytz.utc
cst = pytz.timezone('US/Central')
race_time_utc = utc.localize(race_time)
race_time_cst = race_time_utc.astimezone(cst)

# Display the location and race time in CST
print("Next race location:", location)
print("Race time in CST:", race_time_cst.strftime("%A, %B %d, %Y %I:%M %p"))
