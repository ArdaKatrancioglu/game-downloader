# Authorized Game Downloader

Windows/macOS için PySide6 tabanlı bir web arama ve sıralı parça indirme
uygulamasıdır. Yalnızca sahibi olduğunuz veya indirmeye açıkça yetkili olduğunuz
içeriklerde kullanın.

Uygulama yerel veya uzaktan JSON katalog kullanmaz. Yapılandırılan web sitesinde
arama yapar, seçilen sonucun detay sayfasındaki FuckingFast part bağlantılarını
bulur ve bu part'ları sırayla indirir. Arşiv çıkarma bu sürümde otomatik olarak
başlatılmaz.

## Kurulum ve çalıştırma

Python 3.12 veya üstü gerekir.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e .
authorized-game-downloader
```

`uv` ile:

```bash
uv sync --extra dev
uv run authorized-game-downloader
```

İlk çalıştırmada **Ayarlar** bölümünden web arama adresini ve indirme klasörünü
belirtin. İzin verilen alan adları boşsa web arama adresinin alan adı otomatik
olarak kullanılır.

## Web arama sözleşmesi

`Oyun ara` alanındaki her sorgu aşağıdaki biçimde gönderilir:

```text
GET {web-arama-adresi}/?s={url-encoded-keyword}
```

Arama sonuçlarında yalnızca aşağıdaki yapıya uyan başlık ve bağlantılar kabul
edilir:

```html
<header class="entry-header">
  <div class="entry-meta">...</div>
  <h1 class="entry-title">
    <a href="https://site.example/content" rel="bookmark">Content title</a>
  </h1>
</header>
```

Sonuç listesinde hem başlık hem de detay URL'si gösterilir. Yalnızca seçilen
sonucun aynı izinli alan adındaki detay sayfası alınır. Katalog dosyası, katalog
güncelleme veya katalog içe aktarma akışı yoktur.

## FuckingFast part keşfi

Detay sayfasındaki anchor bağlantıları inert HTML olarak ayrıştırılır. Kabul
edilen bağlantı biçimi:

```text
https://fuckingfast.co/{id}#{contentName}--_.part001.rar
```

Geçerli bağlantılar dosya adına göre tekilleştirilir ve `partNNN` numarasına
göre sıralanır. Başka host'lar, eksik dosya fragment'ları ve güvenli olmayan
dosya adları yok sayılır.

## FuckingFast indirme akışı

Her part için aynı `httpx.AsyncClient` ve cookie jar kullanılarak şu işlemler
tamamlanır:

1. Fragment'sız FuckingFast dosya sayfasına `GET` gönderilir.
2. Sayfadaki tek geçerli `hx-post="/f/<public_file_id>/go"` hedefi bulunur.
3. Bu endpoint'e `HX-Request: true`, `HX-Current-URL` ve `Referer` başlıklarıyla
   boş bir `POST` gönderilir.
4. `200 OK` yanıtındaki `HX-Redirect` başlığının tam olarak HTTPS
   `dl.fuckingfast.co/dl/<opaque_token>` adresine işaret ettiği doğrulanır.
5. Bu URL'ye aynı oturumla normal `GET` gönderilir ve yanıt diske stream edilir.
6. `Content-Disposition: attachment` ve aktarım boyutu doğrulandıktan sonra
   `.part` geçici dosyası atomik olarak gerçek dosya adına çevrilir.

Bir part tamamen indirilip doğrulanmadan sonraki part'ın sayfasına gidilmez.
Hata halinde tamamlanmış önceki part'lar ve yarım `.part` dosyası korunur. Opaque
token istemci tarafında üretilmez veya tahmin edilmez.

Uygulama reklam tıklayıcısı çalıştırmaz, CAPTCHA çözmez ve Cloudflare
doğrulamasını atlatmaz. Sunucu insan/tarayıcı doğrulaması isterse indirme açık
bir hata ile durur.

## Güvenlik ve dosya davranışı

- Arama ve detay sayfaları yalnızca HTTPS ve açık alan adı listesiyle alınır.
- FuckingFast sayfa URL'leri ile final download host'u ayrı ayrı doğrulanır.
- URL içindeki kullanıcı adı/parola, beklenmeyen yönlendirme ve dış host reddedilir.
- Dosyalar belleğe bütünüyle alınmaz; stream edilerek `<filename>.part` içine yazılır.
- Var olan tamamlanmış dosyaların üzerine yazılmaz.
- Uygulama indirilen çalıştırılabilir dosyaları başlatmaz.

## Testler

```bash
uv sync --extra dev
uv run ruff check .
uv run pytest
```

Testler canlı servislere bağlanmaz. Yerel HTML fixture'ları ve
`httpx.MockTransport` ile arama URL'si/seçicileri, part keşfi/sırası, cookie
oturumu, HTMX POST'u, `HX-Redirect`, attachment ve byte sayısı kontrolleri
doğrulanır.

## Paketleme

Platforma özel PyInstaller derlemesi:

```bash
uv sync --extra packaging
uv run pyinstaller authorized_game_downloader.spec
```

Windows için `scripts/build_windows.ps1` kullanılabilir. Windows çıktısını
Windows üzerinde, macOS çıktısını macOS üzerinde üretin.
