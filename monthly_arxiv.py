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
import datetime as dt


today = dt.date.today()
last_day = today.replace(day=1) - dt.timedelta(days=1)
first_day = last_day.replace(day=1)
BACKFILL_RANGE = (first_day.isoformat(), last_day.isoformat())

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
    ('category', pa.string()),
    ('abstract', pa.string()),
    ('published', pa.string()),
    ('url', pa.string()),
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

q_start = first_day.strftime('%Y%m%d%H%M')
q_end = last_day.strftime('%Y%m%d%H%M')
q = f'all:"information retrieval" AND submittedDate:[{q_start} TO {q_end}]'

root = fetch_page(q, 0)
total_results = int(root.find('opensearch:totalResults', ns).text)
entries = root.findall('atom:entry', ns)

start = PAGE_SIZE
while start < total_results:
    time.sleep(3)
    page_root = fetch_page(q, start)
    entries.extend(page_root.findall('atom:entry', ns))
    start += PAGE_SIZE

print(f"{first_day} to {last_day}: {len(entries)}/{total_results} entries")

#create empty list
ids, titles, authors, categories, abstracts, published, urls = [], [], [], [], [], [], []

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

    # add handling for multiple category tags
    category_terms = [c.attrib['term'] for c in entry.findall('atom:category', ns)]
    categories.append('; '.join(category_terms))

    pdf_link_el = entry.find("atom:link[@title='pdf']", ns)
    urls.append(pdf_link_el.attrib['href'] if pdf_link_el is not None else None)

#create pq table using them lists
table = pa.Table.from_arrays(
    [
        pa.array(ids, type=pa.string()),
        pa.array(titles, type=pa.string()),
        pa.array(authors, type=pa.string()),
        pa.array(categories, type=pa.string()),
        pa.array(abstracts, type=pa.string()),
        pa.array(published, type=pa.string()),
        pa.array(urls, type=pa.string()),
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

# append to existing backfill instead of overwriting it
existing_file_info = s3_filesystem.get_file_info(s3_path)
if existing_file_info.type != fs.FileType.NotFound:
    existing_table = pq.read_table(s3_path, filesystem=s3_filesystem)
    table = pa.concat_tables([existing_table, table])
    table = table.sort_by([('published', 'ascending')])

pq.write_table(table, s3_path, filesystem=s3_filesystem)

file_info = s3_filesystem.get_file_info(s3_path)
if file_info.type != fs.FileType.NotFound:
    print(f"Backfill completed. raw-arvix-entries.parquet has been uploaded to s3://{s3_path}")
else:
    print(f"Backfill failed. raw-arvix-entries.parquet not found at s3://{s3_path}")