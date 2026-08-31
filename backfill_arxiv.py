import urllib.parse, urllib.request
import xml.etree.ElementTree as ET
import csv
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pyarrow.fs as fs
import pyarrow.compute as pc
import time
import os

BACKFILL_RANGE = ('2024-01-01', '2026-06-30')
quarters = pd.period_range(start=BACKFILL_RANGE[0], end=BACKFILL_RANGE[1], freq='Q')

BASE_URL = "http://export.arxiv.org/api/query?"
HEADERS = {"User-Agent": "MyArxivClient/1.0 (martyshort52@gmail.com)"}
PAGE_SIZE = 100

# add opensearch to max out rows to write
ns = {
    'atom': 'http://www.w3.org/2005/Atom',
    'opensearch': 'http://a9.com/-/spec/opensearch/1.1/',
}

# lay out schema for pq
schema = pa.schema([
    ('id', pa.string()),
    ('title', pa.string()),
    ('author', pa.string()),
    ('abstract', pa.string()),
    ('published', pa.string()),
    ('link', pa.string()),
])

# loop over each search page
def fetch_page(q, start, retries=10):
    params = {'search_query': q, 'start': start, 'max_results': PAGE_SIZE}
    url = BASE_URL + urllib.parse.urlencode(params)
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req) as resp:
                return ET.fromstring(resp.read().decode('utf-8'))
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 503) and attempt < retries - 1:
                # arXiv sends 429 when we're going too fast; back off harder than 500/503
                wait = int(e.headers.get('Retry-After', min(60, 15 * (attempt + 1))))
                time.sleep(wait)
                continue
            raise

# loop over each quarter so a long backfill doesn't ride on one giant date range
entries = []
for quarter in quarters:
    q_start = quarter.start_time.strftime('%Y%m%d%H%M')
    q_end = quarter.end_time.strftime('%Y%m%d%H%M')
    q = f'all:"information retrieval" AND submittedDate:[{q_start} TO {q_end}]'

    root = fetch_page(q, 0)
    total_results = int(root.find('opensearch:totalResults', ns).text)
    quarter_entries = root.findall('atom:entry', ns)

    start = PAGE_SIZE
    while start < total_results:
        time.sleep(3)
        page_root = fetch_page(q, start)
        quarter_entries.extend(page_root.findall('atom:entry', ns))
        start += PAGE_SIZE

    print(f"{quarter}: {len(quarter_entries)}/{total_results} entries")
    entries.extend(quarter_entries)
    time.sleep(5)

#create empty list
ids, titles, authors, abstracts, published, links = [], [], [], [], [], []

# loop over each entry/paper
for entry in entries:

    # fill in the list of columns based on tag
    ids.append(entry.find('atom:id', ns).text.strip())
    titles.append(entry.find('atom:title', ns).text.strip().replace('\n', ' '))
    abstracts.append(entry.find('atom:summary', ns).text.strip().replace('\n', ' '))
    published.append(entry.find('atom:published', ns).text.strip())

    # add handling for multiple author tags
    author_names = [a.find('atom:name', ns).text for a in entry.findall('atom:author', ns)]
    authors.append('; '.join(author_names))

    link = entry.find('atom:id', ns).text.strip()  # or filter atom:link[@rel='alternate']
    links.append(link)

#create pq table using them lists
table = pa.Table.from_arrays(
    [
        pa.array(ids, type=pa.string()),
        pa.array(titles, type=pa.string()),
        pa.array(authors, type=pa.string()),
        pa.array(abstracts, type=pa.string()),
        pa.array(published, type=pa.string()),
        pa.array(links, type=pa.string()),
    ],
    schema=schema,
)

table = table.set_column(
    table.schema.get_field_index('published'),
    'published',
    pc.strptime(table.column('published'), format='%Y-%m-%dT%H:%M:%SZ', unit='s'),
)
table = table.sort_by([('published', 'ascending')])

# push to s3
s3_filesystem = fs.S3FileSystem(region= "ap-southeast-1")

s3_path = "arvix-db/raw-arxiv/raw-arvix-entries.parquet"

pq.write_table(table, s3_path, filesystem=s3_filesystem)