#python base image
FROM python:3.12-slim
# create/chdir
WORKDIR /app
# copy files
COPY backfill_arxiv.py requirements.txt  .
# install packages
RUN pip install -r requirements.txt
# shit to run when container starts
CMD ["python", "backfill_arxiv.py"]