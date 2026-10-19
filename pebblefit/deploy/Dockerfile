FROM python:3.13-slim
WORKDIR /code
RUN pip install --no-cache-dir flask==3.1.0 gunicorn==23.0.0 paho-mqtt==2.1.0 websocket-client==1.8.0 "psycopg[binary]==3.2.3"
COPY app.py fitness.py training.py hrv.py recovery.py dashboard.py watch_api.py weight.py physiology.py /code/
COPY web /code/web
# receiver: uploads + Home Assistant bridge (port 9998); web: dashboard (port 8790)
CMD exec gunicorn -w 1 -b 0.0.0.0:9998 --access-logfile - app:app
