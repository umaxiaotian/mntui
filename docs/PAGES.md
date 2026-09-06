# GitHub Pages

The static site lives in `site/`. English is served at `/` and Japanese at `/ja/`.
Both pages share `site/style.css`; no build tools or JavaScript are required.
Update both HTML files when changing the content.

## Preview locally / ローカルで確認

From the repository root / リポジトリのルートで実行:

```bash
python3 -m http.server 8000 --directory site
```

Open <http://localhost:8000/> (English) or <http://localhost:8000/ja/> (日本語).

## Publish / 公開

1. In the repository's **Settings → Pages → Build and deployment**, set
   **Source** to **GitHub Actions**.
2. Push the site and workflow to `main`. The **GitHub Pages** workflow deploys
   changes to `site/` automatically. It can also be run using **Actions → GitHub
   Pages → Run workflow** on `main`.
3. After a successful deployment, open <https://umaxiaotian.github.io/mntui/>.
   Japanese is available at <https://umaxiaotian.github.io/mntui/ja/>.

1. リポジトリの **Settings → Pages → Build and deployment** で、
   **Source** を **GitHub Actions** に設定します。
2. サイトとワークフローを `main` にpushすると、**GitHub Pages** ワークフローで
   公開されます。以降は `site/` の変更時に自動更新されます。
   **Actions → GitHub Pages → Run workflow** から `main` を選んで手動実行もできます。
3. デプロイが成功したら、上記の公開URLを開いて確認します。

The screenshot uses the existing GitHub attachment linked in the README.

Reference: [GitHub Pages custom workflows](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages).
