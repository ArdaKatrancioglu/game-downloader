# Authorized Game Downloader

Windows/macOS için PySide6 tabanlı bir web arama ve sıralı parça indirme
uygulamasıdır. Yalnızca sahibi olduğunuz veya indirmeye açıkça yetkili olduğunuz
içeriklerde kullanın.

Uygulama yapılandırılan web sitesinde arama yapar. HTML elemanlarının `listing`
özelliğindeki JSON metadatasını okur ve varsa seçilen Download ID için görünür
bir Chrome/Chromium oturumunda geçici indirme adresi üretir. İndirme seçenekleri
metadata içinde yoksa oyun sayfasındaki gerçek indirme modalından keşfedilir.

## Kurulum ve çalıştırma

Python 3.12 veya üstü gerekir.

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
python -m pip install -e .
playwright install chromium
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
GET {web-arama-adresi}/search/{url-encoded-keyword}
```

Her sonuç elemanının `listing` özelliği JSON olmalıdır. Örnek:

```html
<article listing='{"id":42,"title":"Demo","slug":"demo",
  "imageurl":"https://site.example/demo.jpg",
  "coverurl":"https://site.example/demo-cover.jpg","size_gb":1.5,
  "release_date":"2026-01-01","vote_average":"v1.2.3",
  "genres":[{"id":1,"name":"Action"}],
  "downloads":[{"id":4584,"name":"demo.zip","size":1234}]}'></article>
```

`slug`, `{web-arama-adresi}/game/{slug}` detay URL'sine dönüştürülür. Sonuç
listesinde başlık, ID, boyut, yayın tarihi, türler, görsel/kapak ve detay URL'si
gösterilir. Bozuk bir `listing` kaydı uyarı olarak loglanır ve diğer sonuçların
işlenmesini engellemez. Yalnızca seçilen
sonucun aynı izinli alan adındaki detay sayfası alınır. Katalog dosyası, katalog
güncelleme veya katalog içe aktarma akışı yoktur.

## Tarayıcıdan doğrudan indirme

**Ayarlar** içinde Chrome çalıştırılabilir dosyası boş bırakılırsa Playwright'ın
Chromium'u kullanılır. Sistem Google Chrome veya Chrome for Testing kullanılacaksa
çalıştırılabilir dosyanın tam yolunu girin. Varsayılan mod görünürdür; “Arka planda
çalıştır” seçeneği headless modu açar. Zaman aşımı 1–300 saniye arasında ve
indirme klasörü aynı ekrandan ayarlanır. İlk açılışta macOS ve Windows kullanıcısının
sistem Downloads klasörü seçilir. Aynı değerler sırasıyla
`GAME_DOWNLOADER_CHROME_EXECUTABLE_PATH`, `GAME_DOWNLOADER_BROWSER_HEADLESS`,
`GAME_DOWNLOADER_BROWSER_TIMEOUT_SECONDS` ve
`GAME_DOWNLOADER_DEFAULT_DOWNLOAD_FOLDER` ortam değişkenleriyle de verilebilir.

Bir arama sonucu seçilip **İndir** düğmesine basıldığında tarayıcı aynı işlem
içinde modalı açar, ilk görünür Download kaydını seçer ve indirmeyi başlatır.
Kullanıcıdan Download ID seçmesi istenmez. Chrome'un normal sayfa akışından alınan
geçici adres mevcut `DownloadManager` tarafından aktarılır; böylece boyut, yüzde,
hız ve ETA doğru hesaplanır, hız sınırı ile duraklat/devam çalışır. Chrome penceresi
işlem boyunca açık kalır. Dosya önce `.part` olarak kaydedilir ve tamamlanınca
atomik olarak `{ayar-indirme-klasörü}/{contentTitle}/{dosya-adı}` yoluna taşınır.

## Güvenlik ve dosya davranışı

- Arama ve detay sayfaları yalnızca HTTPS ve açık alan adı listesiyle alınır.
- Oyun sayfası ve final download host'u ayrı ayrı doğrulanır.
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
`httpx.MockTransport` ve Playwright taklitleri ile arama URL'si, `listing`
ayrıştırması, modal Download ID keşfi, CSRF yenilemesi, ilerleme sinyalleri ve
byte sayısı kontrolleri doğrulanır.

## Paketleme

Platforma özel PyInstaller derlemesi:

```bash
uv sync --extra packaging
uv run pyinstaller ipsum_indirici.spec
```

Windows'ta `scripts/build_windows.ps1` çalıştırıldığında Python, PySide6 ve
diğer çalışma zamanı bağımlılıklarını içeren tek bir
`dist/IpsumIndirici.exe` üretilir. Windows çıktısını Windows üzerinde,
macOS çıktısını macOS üzerinde üretin.

macOS geliştiricileri GitHub'daki **Actions > Windows EXE > Run workflow**
akışını çalıştırabilir. İş tamamlandığında **IpsumIndirici-Windows** artifact'ı
indirilir; içindeki EXE hedef bilgisayarda Python kurulumu gerektirmez.
