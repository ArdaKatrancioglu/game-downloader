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

**Ayarlar** içindeki “On-demand: HTTP Range ile ZIP'i indirirken çıkar” seçeneği
aktifse uygulama ZIP dizinini uzaktan okur ve üyeleri gereken bloklar geldikçe
hedef klasöre çıkarır. Range aktarımı 8 MiB bloklar kullanır ve mevcut blok
çıkarılırken sıradaki bloğu arka planda önceden getirir. Kaldığı yerden devam
edebilmek için kullanılan disk cache'i LRU olarak en fazla 512 MiB tutulur;
tam ZIP bu yolda diske yazılmaz. Bu nedenle peak disk kullanımı yaklaşık olarak
çıkarılmış içerik boyutu + 512 MiB çalışma alanıdır. Cache sınırından çıkarılan
yarım bir ZIP üyesi kesintiden sonra yeniden indirilebilir, tamamlanmış üyelerse
CRC checkpoint'i sayesinde atlanır. Seçenek kapalıysa ZIP tek
sıralı bağlantıyla indirilir ve otomatik çıkarma açıksa indirme tamamlandıktan
sonra çıkarılır. Sunucu Range isteklerini desteklemiyorsa da bu sıralı akışa
otomatik dönülür. RAR, 7z ve TAR arşivleri normal indirme yolunu kullanır.
ZIP dizini okunduktan sonra üyelerin `file_size` değerleri toplanır; kesin
açılmış boyut, yaklaşık peak kullanım ve mevcut checkpoint düşüldükten sonra
gereken ek boş alan loglanıp yeniden denetlenir. Katalog boyutu bu kesin
kontrolden önce yalnızca ilk tahmin olarak kullanılır. Ana ekrandaki **İndir**
düğmesi önce geçici URL'yi ve Central Directory metadata'sını hazırlar; oyun
seçenekleri popup'ı kesin açılmış boyut ve peak alan öğrenildikten sonra açılır.
Bulk aktarım ancak bu popup onaylandıktan sonra başlar.

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

Windows'ta `scripts/build_windows.ps1` çalıştırıldığında Python, PySide6,
`uv.lock` ile eşleşen Playwright Chromium, 7-Zip ve diğer çalışma zamanı
bağımlılıklarını içeren tek bir `dist/IpsumIndirici.exe` üretilir. Hedef
bilgisayarda Python, Playwright, Chrome, WinRAR veya 7-Zip kurulması gerekmez.
Gömülü Chromium nedeniyle EXE büyük olur ve ilk açılışta geçici klasöre
çıkarılması zaman alabilir.
Windows çıktısını Windows üzerinde, macOS çıktısını macOS üzerinde üretin.

### Windows'ta tek EXE oluşturma

Repoyu Windows bilgisayara klonladıktan sonra proje klasöründe PowerShell açın
ve aşağıdaki bloğu doğrudan kopyalayıp çalıştırın:

```powershell
winget install --id astral-sh.uv --exact --accept-package-agreements --accept-source-agreements
$env:Path = [Environment]::GetEnvironmentVariable("Path", "User") + ";" + [Environment]::GetEnvironmentVariable("Path", "Machine")
git pull
powershell -ExecutionPolicy Bypass -File .\scripts\build_windows.ps1
```

İşlem tamamlandığında taşınabilir çıktı `dist\IpsumIndirici.exe` konumunda
olur. `uv` zaten kuruluysa ilk iki komut atlanabilir.

macOS geliştiricileri GitHub'daki **Actions > Windows EXE > Run workflow**
akışını çalıştırabilir. İş tamamlandığında **IpsumIndirici-Windows** artifact'ı
indirilir; içindeki EXE hedef bilgisayarda Python veya Chrome kurulumu gerektirmez.
