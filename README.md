# Vietnam Calendar

ベトナムの祝日やイベントを確認するためのカレンダーです。

## Dockerで起動

```bash
docker build -t vietnam-calendar:local .
docker run --rm -p 8080:80 vietnam-calendar:local
```

ブラウザで <http://localhost:8080> を開きます。
