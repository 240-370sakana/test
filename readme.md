地震津波監視統合Webサイト QuakeView
2026/05/05

【ローカルサーバ実行方法】
python QuakeView/server/quakeview_proxy.py
バックグラウンドで実行する場合: 
Start-Process python -ArgumentList "QuakeView/server/quakeview_proxy.py" -WindowStyle Hidden


【ファイル構造】
QuakeView/
│
├── index.html                  ← トップページ
│
├── web/                        ← フロントエンド（単機能HTMLダッシュボード群）
│   ├── hypo_map_viewer.html
│   ├── earthquake_viz.html
│   ├── elapsed_timer.html
│   └── realtime_map.html
│
│
├── personal/                        ← フロントエンド（統合版HTMLダッシュボード群）
│   ├── realtime.html リアルタイム地震津波情報
│   ├── data-jp.html 日本周辺 地震津波データ検索(未作成)
│   └── data-world.html 全世界 地震津波データ検索(未作成)
│
├── css/
│   ├── quakeview.css   共通CSS
│   ├── earthquake_viz.css
│   ├── elapsed_timer.css
│   ├── hypo_map_viewer.css
│   └── rtm_additions.css
│
├── sounds/
│   ├── EEW_canceled.wav    EEW(緊急地震速報)キャンセル報時のアナウンス
│   ├── EEW1.wav    EEW予報発表時効果音
│   ├── EEW2.wav    EEW警報発表時効果音
│   ├── PGA1.wav    最大加速度1gal以上効果音
│   ├── PGA2.wav    最大加速度200gal以上効果音
│   ├── Shindo0.wav    RI(リアルタイム震度)0.0以上効果音
│   ├── Shindo1.wav    RI0.5以上効果音
│   ├── Shindo2.wav    RI4.5以上効果音
│   ├── Tsunami_1.wav    津波注意報発表時アナウンス
│   ├── Tsunami_2.wav    津波警報発表時アナウンス
│   ├── Tsunami_3.wav    大津波警報発表時アナウンス
│   └── Tsunami_lifted.wav    津波情報全解除時アナウンス
│
├── py/
│   ├── hinet_jma_scraper.py     ← Hi-net POST スクレイパー
│   └── importers/
│       └── jma_hypo_importer.py     ← JMA固定長フォーマット→MySQL
│
├── analysis/
│   ├── __pycache__/
│   ├── output/
│   │   └── etas_output.json ← ETAS解析後の解析結果ファイル
│   ├── catalog.csv ← 可視化する震源データ(現状、必ずしも必要ではない)
│   └── etas_analysis.py ← ETAS解析をするファイル
│
│
├── config/                     ← 設定ファイル
├── Downloads/
│   └── stations.csv  ← 強震モニタの観測点座標
│
├── server/
│   └── quakeview_proxy.py  ← ローカルサーバを立てるコードファイル　かつ　Early-estからxmlデータを取得するコードファイル
│
└── README.md


【制作時の注意点】
単体でも動くが、依存ゼロで、QuakeView_personalにそのまま差し込める

・既存システムに統合する前提で設計すること
・グローバル変数に依存しないこと
・すべての関数は引数でデータを受け取ること
・外部状態（selectedEvent, mapなど）を直接参照しないこと

・必ず以下の構造で出力すること：

① 純粋ロジック関数（副作用なし）
② 描画関数（DOM操作のみ）
③ 統合用エントリ関数（onEventSelected など）

・時間はUNIXミリ秒で扱うこと
・地図は Leaflet の map インスタンスを引数で受け取ること

・既存関数を書き換えないことを前提にすること
・「どこにどう統合するか」を最後に説明すること

・関数は再利用可能にすること

・副作用を持つ関数を分離すること

・setIntervalは定義しないこと（既存ループに統合するため）

・初期化処理を勝手に実行しないこと

・スタイルはすべて外部CSS（quakeview.css）に依存すること
・HTML内に<style>は書かないこと
・色やフォントはCSS変数（--cyanなど）を使うこと
・新しいスタイルが必要ならクラスだけ定義し、CSSは書かないこと



【Issues】
- 通知機能の追加
    - ページ内ポップアップ
    - windows通知
- データベース名、テーブル名の改名？(わかりずらい？)
- Lotus地震活動・津波履歴ビューアのhtml化
    - 過去の大地震のすべり分布も欲しい
- 津波伝播時間を即時計算したい
- 津波逆伝播計算もしたい
- J-RISQ受信する
- 長周期モニタ作る
- リアルタイム検索　構想練る
- 津波情報　表示させる
- 検潮データ見れるようにする
- USGS地震情報　表示
- スマホ版構想練る
- 簡易断層マグニチュード計算機
- データベース軽量化(全期間のデータを使用しようとするとエラーを吐く)
- 家PCをサーバに、学校PCをユーザ側に入れ替える
- 強震モニタ作る
- AQUA受信する
- QuakeView再構築
- 震度予測AIの開発
- 速報値震源マップを可視化ビューに統合する