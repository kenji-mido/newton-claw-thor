# newton-claw on DGX Spark (Docker)

Jetson Thor 向けの `setup_thor.sh` 相当を、DGX Spark (GB10 / aarch64 / CUDA 13) 上のコンテナに閉じ込めたもの。
ホストには venv も Ollama も入れない。ブラウザのチャットから学習・再生を指示でき、右にビューアが出る。

**Kubernetes で 1 つの Pod として動かす版 (このリポジトリの外) と、同じ 3 つのイメージを同じ設定で動かす。**
片方を変えたらもう片方も揃える。

| サービス | イメージ | 役割 |
|---|---|---|
| `newton` | `newton-claw:spark` (`docker/Dockerfile`) | 制御サーバー (:5561、GPU ジョブは同時に 1 つ) + GPU ジョブ。`--web` のジョブは viser を :8090 で開く。起動時に `AUTOSTART` を 1 回実行 (`docker/entrypoint.sh`) |
| `ollama` | `ollama/ollama:0.35.1` | Gemma 4 12B (`gemma4:12b`、volume `newton-claw_ollama-models`) |
| `webchat` | `newton-claw-webchat:spark` (`docker/webchat/`) | ブラウザのチャット + viser を `/viewer/` で中継 (WebSocket 込み) |

## 開く
| | Docker 版 (サインイン無し) |
|---|---|
| このマシン | http://127.0.0.1:8095 |
| LAN / Tailscale | `docker/compose.override.yaml` に書いたアドレスの :8095 |
| ビューアだけ | 上の URL + `/viewer/` |

起動すると Go2 (参照ポリシー) が歩いている (`AUTOSTART`、`""` で何もしない)。
最初のチャットの返答はモデルのロードで 1〜2 分。以降は 1 往復 10〜60 秒。

## 起動 / 停止
```bash
# リポジトリの一番上で。LAN / Tailscale からも開くなら、例を写してこのマシンのアドレスを書く (git には入らない)
cp docker/compose.override.example.yaml docker/compose.override.yaml
docker compose -f docker/compose.yaml -f docker/compose.override.yaml up -d --build
docker compose -f docker/compose.yaml -f docker/compose.override.yaml down   # 停止 (volume は残る)
# このマシンだけで使うなら -f docker/compose.yaml だけでよい
```

## Web チャット
左がチャット、右にビューア。サンプル指示文 81 件 (10 カテゴリ、日本語/英語) をプルダウン or「全サンプル一覧」
(絞り込み検索つき) から選んで入力欄に入れ、編集して送信できる。サンプルは `docker/webchat/samples.json` を
編集して増やせる (再ビルド要: `up -d --build webchat`)。状態表示の横の「ログ」ボタンでジョブのログ (3 秒ごと更新)。

`webchat/server.py` は `agents/chat.py` をそのまま import し、同じ `newton__*` ツール (`newton-mcp.mjs`) と案内文で
動く。`agents/chat.py` に対する変更点 (webchat 側だけで、リポジトリ本体は無改変):
- モニタが無いので、ツール引数の `view: gui` を `web` に書き換え、案内文にもその旨を追記
- ジョブ起動直後に失敗していないか確認し (train/play の準備完了の行が出るまで、最大 90 秒)、失敗していればログ末尾を
  モデルに返す。例: 未学習の ANYmal C を「歩かせて」→ checkpoint 無しで落ちる → モデルが `pretrained:true` で再実行
- 1 リクエストあたりのツール呼び出し上限を 4 → 6 (「409 → 止める → 再実行」で 4 を超えるため)
- ツール `newton__job_logs` を追加 (制御サーバーの `GET /logs` を webchat が直接呼ぶ)。「ログを見せて」「エラーは出てる？」など
- 1 回のチャットは `POST /api/chat` で始めて `GET /api/chat/<id>` を 1 秒ごとに見る (HTTPS のゲートウェイ、例えば Envoy の既定の
  タイムアウト 15 秒を超えるため)。ツールの呼び出しは途中経過として画面に出る
- ビューアは `/viewer/` で中継 (HTTPS の gateway の下では `http://<host>:8090` の iframe をブラウザが止めるため)。
  viser のクライアントはページの URL から WebSocket の URL を作る (末尾の `/` を落として `/viewer`) ので、`/viewer` の upgrade も中継する

## CLI (コンテナ内で実行)
```bash
alias claw='docker exec newton-claw claw'
claw tasks
claw train --task Newton-Cartpole-v0 --headless
claw status
claw eval  --task Newton-Cartpole-v0
claw play  --task Newton-Velocity-Flat-Unitree-Go2-v0 --pretrained --web
claw logs
claw close                                           # GPU ジョブは同時に 1 つだけ
```
GL ウィンドウ (既定の `gl`) は使わない。`NEWTON_DEFAULT_VIEWER=viser` なので、フラグなしの train/play もブラウザ表示。
`claw` が返す `view_url` (http://172.29.x.x:8090) はコンテナ内部の IP なので使わない。

## ポートと公開範囲 (Docker 版)
| ポート | 用途 | 公開先 |
|---|---|---|
| 8095 | Web チャット + ビューア (`/viewer/`)、認証なし | 127.0.0.1。LAN / Tailscale は `docker/compose.override.yaml` で足す |
| 5561 | 制御 API (認証なし) | 127.0.0.1 のみ (webchat は compose ネットワーク経由) |
| 8090 | viser 直接 (デバッグ用) | 127.0.0.1 のみ |
| 8001 | Ollama (デバッグ用) | 127.0.0.1 のみ。ホストの :8000 は別のサービスが使用中 |

## データの置き場所
| 何 | Docker 版 |
|---|---|
| チェックポイント / ONNX | `newton_lab/logs/rsl_rl/<task>/<run>/` (リポジトリ内、gitignore 済み) |
| ジョブログ | `newton_sim/_control_logs/` |
| Newton アセット + Warp カーネルキャッシュ | volume `newton-claw_newton-cache` |
| Ollama のモデル | volume `newton-claw_ollama-models` |

## 上流 (Thor 版) との差分
- ベースは `nvidia/cuda:13.0.3-base-ubuntu24.04`。torch cu130 の aarch64 wheel は sm_120 までだが、GB10 (sm_121) で動作する。
- **`libpython3.12` を追加。** aarch64 では Newton の USD 読み込みが `usd-core` ではなく `usd-exchange` を使い、これが libpython の共有ライブラリを要求する (無いと Go2 などのロードで `Failed to import pxr`)。
- **viser の既定ポートを 8080 → `NEWTON_VISER_PORT` (8090) に変更 (イメージ内の Newton だけ)。**
  `claw open` (= `python -m newton.examples <robot>`) にはポート指定が無く、ブラウザ表示が :8080 で
  立ち上がっていた (train/play は :8090)。このホストの :8080 は OpenShell が使用中。Newton の
  ソースが変わってパッチが当たらなくなったらビルドが失敗する。上流 (Thor) でも同じ食い違いがあるはず。
- リポジトリのコードは一切改変していない (追加は `docker/` と `.dockerignore` だけ)。

## 動作確認
| 日付 | 項目 | 結果 |
|---|---|---|
| 10-05 | self-test | newton 1.6.0 / warp 1.18.0 (arch 121) / torch 2.14.1+cu130 / GB10 |
| 10-05 | Cartpole 学習 (4096 envs, 150 iter) | 平均エピソード長 300 (上限), ~211k env-steps/s |
| 10-05 | Cartpole eval (1024 envs) | falls 0, ~274k env-steps/s |
| 10-05 | Web チャット (日本語サンプル 15 件ほど) | 一覧/学習/進捗/再生 (速度指定・横歩き・旋回)/評価/表示/チューニング/停止 すべて正しいツールに振り分け |
| 10-05 | Go2 参照ポリシー eval (チャット経由) | 1024 体で転倒 17、追従誤差 0.13 m/s (Thor の README: 18 / 0.13) |
| 10-05 | G1 学習 (2048 envs, 1000 iter、チャット経由、`view: web`) | 約 55 分、平均報酬 58.0、平均エピソード長 979 |
| 10-06 | ビューアの中継 `/viewer/` (Docker 版・Kubernetes 版の Pod) | HTTP 200、WebSocket 101 (直接接続と同じ応答) |
| 10-06 | Kubernetes 版 (Pod 3/3、port-forward で) | チャット → ANYmal C 再生 (checkpoint 無し → `pretrained` で再実行) |
| 10-06 | 実ブラウザで Docker 版 (LAN のアドレスの :8095) | 右のビューアに Go2 が映る (中継経由で viser に接続、`Connection opened`) |

未確認: Kubernetes 版の HTTPS ゲートウェイ経由 (サインインが必要)。ヘッドレスのブラウザは WebGL が動かず 3D の描画は確認できないので、実ブラウザで見る。

60 iter / 1024 envs (README の Thor 計測条件) では Cartpole は未収束 (eval 平均 2.13 s / 5.0 s) だった。既定の 150 iter で収束する。
