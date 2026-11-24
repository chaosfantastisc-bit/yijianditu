using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Net;
using System.Net.Http;
using System.Runtime.InteropServices;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;

namespace Yijianditu
{
    internal sealed class TileSource
    {
        public static readonly TileSource ArcGis = new TileSource("arcgis_satellite", "ArcGIS 卫星",
            "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
            null, null, 19);
        public static readonly TileSource TianDiTu = new TileSource("tianditu_satellite", "天地图 影像",
            TianDiTuUrl("img"), TianDiTuUrl("cia"), "https://www.tianditu.gov.cn/", 18);
        public static readonly TileSource[] All = { TianDiTu, ArcGis };

        public readonly string Id, Name, Url, LabelUrl, Referer;
        public readonly int MaxZoom;
        public bool HasSubdomains { get { return Url.Contains("{s}"); } }

        public TileSource(string id, string name, string url, string labelUrl, string referer, int maxZoom)
        {
            Id = id; Name = name; Url = url; LabelUrl = labelUrl; Referer = referer; MaxZoom = maxZoom;
        }

        private static string TianDiTuUrl(string layer)
        {
            return "https://t{s}.tianditu.gov.cn/" + layer + "_w/wmts?SERVICE=WMTS&REQUEST=GetTile" +
                "&VERSION=1.0.0&LAYER=" + layer + "&STYLE=default&TILEMATRIXSET=w&FORMAT=tiles" +
                "&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}&tk={tk}";
        }

        public Uri BuildUrl(int x, int y, int zoom, string token, bool label = false)
        {
            return new Uri((label ? LabelUrl : Url)
                .Replace("{z}", zoom.ToString()).Replace("{x}", x.ToString()).Replace("{y}", y.ToString())
                .Replace("{s}", ((x + y) % 8).ToString()).Replace("{tk}", Uri.EscapeDataString(token ?? "")));
        }

        public static TileSource Find(string id)
        {
            foreach (var source in All) if (source.Id == id) return source;
            throw new ArgumentException("未知图源");
        }

        public object Describe()
        {
            return new {
                id = Id, name = Name, min_zoom = 1, max_zoom = MaxZoom, has_label = LabelUrl != null,
                attribution = this == ArcGis ? "© Esri" : "© 天地图",
                preview_url = this == ArcGis ? Url : null,
                subdomains = HasSubdomains ? new[] {"0","1","2","3","4","5","6","7"} : new string[0],
            };
        }
    }

    internal static class TileHttp
    {
        private sealed class RequestFailure : IOException
        {
            public readonly bool Transient;
            public RequestFailure(string message, bool transient) : base(message) { Transient = transient; }
        }

        public sealed class RateLimited : IOException
        {
            public RateLimited() : base("图源限制了请求频率（HTTP 429），请稍后再试") { }
        }

        private static readonly HttpClient Client = MakeClient();

        private static HttpClient MakeClient()
        {
            ServicePointManager.DefaultConnectionLimit = 32;
            var handler = new HttpClientHandler { AllowAutoRedirect = false };
            var client = new HttpClient(handler) {
                Timeout = Timeout.InfiniteTimeSpan, MaxResponseContentBufferSize = 2 * 1024 * 1024,
            };
            client.DefaultRequestHeaders.UserAgent.ParseAdd("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/126.0.0.0 Safari/537.36");
            client.DefaultRequestHeaders.Accept.ParseAdd("image/jpeg,image/png");
            return client;
        }

        public static byte[] Fetch(Uri url, string referer, bool preview, CancellationToken token)
        {
            int attempts = preview ? 1 : 2;
            for (int attempt = 0; ; attempt++)
            {
                token.ThrowIfCancellationRequested();
                try
                {
                    return FetchOnce(url, referer, preview ? 6000 : 12000, token);
                }
                catch (RequestFailure e)
                {
                    if (!e.Transient || attempt + 1 == attempts) throw;
                }
                if (token.WaitHandle.WaitOne(350)) token.ThrowIfCancellationRequested();
            }
        }

        private static byte[] FetchOnce(Uri url, string referer, int timeout, CancellationToken token)
        {
            using (var deadline = CancellationTokenSource.CreateLinkedTokenSource(token))
            using (var request = new HttpRequestMessage(HttpMethod.Get, url))
            {
                deadline.CancelAfter(timeout);
                if (referer != null) request.Headers.Referrer = new Uri(referer);
                try
                {
                    using (var response = Client.SendAsync(request, HttpCompletionOption.ResponseContentRead,
                        deadline.Token).GetAwaiter().GetResult())
                    {
                        int status = (int)response.StatusCode;
                        if (status == 429) throw new RateLimited();
                        if (!response.IsSuccessStatusCode)
                        {
                            bool retry = (status == 500 || status == 502 || status == 503 || status == 504)
                                && response.Headers.RetryAfter == null;
                            throw new RequestFailure("HTTP " + status, retry);
                        }
                        var bytes = response.Content.ReadAsByteArrayAsync().GetAwaiter().GetResult();
                        if (ContentType(bytes) == null) throw new RequestFailure("图源返回内容不是 JPG 或 PNG", false);
                        return bytes;
                    }
                }
                catch (OperationCanceledException)
                {
                    token.ThrowIfCancellationRequested();
                    throw new RequestFailure("瓦片请求超时", true);
                }
                catch (HttpRequestException e)
                {
                    var web = e.InnerException as WebException;
                    throw new RequestFailure(web == null ? "连接异常" : web.Status.ToString(), true);
                }
            }
        }

        public static string ContentType(byte[] data)
        {
            if (data.Length >= 3 && data[0] == 255 && data[1] == 216 && data[2] == 255) return "image/jpeg";
            if (data.Length >= 8 && data[0] == 137 && data[1] == 80 && data[2] == 78 && data[3] == 71) return "image/png";
            return null;
        }
    }

    internal static class MapJob
    {
        private static readonly Regex UnsafeName = new Regex(@"[^0-9A-Za-z_\-\u4e00-\u9fff]+");

        public static object Run(TileGrid grid, OutputGrid output, Target target, TileSource source,
            string directory, string name, string key, CancellationToken token, Action<double, string> report)
        {
            var timer = Stopwatch.StartNew();
            string stem = UnsafeName.Replace((name ?? "").Trim(), "_").Trim('_');
            if (stem.Length == 0)
            {
                string prefix = source.Id == TileSource.TianDiTu.Id ? "TDT" : "ARC";
                stem = prefix + "_z" + grid.Zoom + "_" + DateTime.Now.ToString("MMdd_HHmmss_fff");
            }
            directory = Path.GetFullPath(directory);
            Directory.CreateDirectory(directory);
            string jpg = Path.Combine(directory, stem + ".jpg");
            string dxf = Path.Combine(directory, stem + ".dxf");
            string temporary = Path.Combine(directory, ".yijianditu-" + Guid.NewGuid().ToString("N"));
            string tempJpg = temporary + ".jpg", tempDxf = temporary + ".dxf";
            Size jpegSize;
            int missing;
            try
            {
                var bounds = output.Bounds;
                Bitmap projected;
                using (var mosaic = Download(grid, source, key, token, report, out missing))
                {
                    report(57, "重投影：" + target.Label);
                    projected = Raster.Warp(mosaic, grid, output, target, token);
                }
                using (projected)
                {
                    report(78, "生成 JPG（上限 30 MB）");
                    jpegSize = Raster.SaveJpeg(projected, tempJpg, token);
                }
                token.ThrowIfCancellationRequested();
                report(92, "生成 DXF");
                CadImage.Write(tempDxf, jpg, bounds, jpegSize);
                token.ThrowIfCancellationRequested();
                Publish(tempJpg, jpg, tempDxf, dxf, temporary + ".bak");
                return new {
                    success = true, jpg_path = jpg, dxf_path = dxf, target_crs = target.Label,
                    ll = new[] {bounds[0], bounds[1]}, ur = new[] {bounds[2], bounds[3]},
                    size_m = new[] { Math.Round(bounds[2] - bounds[0], 2), Math.Round(bounds[3] - bounds[1], 2) },
                    pixels = new[] {jpegSize.Width, jpegSize.Height}, missing_tiles = missing,
                    elapsed = Math.Round(timer.Elapsed.TotalSeconds, 1),
                };
            }
            finally
            {
                RemoveTemporary(tempJpg);
                RemoveTemporary(tempDxf);
            }
        }

        private static Bitmap Download(TileGrid grid, TileSource source, string key, CancellationToken token,
            Action<double, string> report, out int missing)
        {
            var image = new Bitmap(grid.Width, grid.Height, PixelFormat.Format24bppRgb);
            int total = (int)grid.TileCount, failed = 0, done = 0, limited = 0;
            var clock = Stopwatch.StartNew();
            long lastUpdate = -250;
            string lastError = null;
            report(1, "开始下载 " + total + " 张瓦片…");
            try
            {
                using (var stop = CancellationTokenSource.CreateLinkedTokenSource(token))
                using (var canvas = Graphics.FromImage(image))
                {
                    canvas.Clear(Color.Black);
                    canvas.InterpolationMode = InterpolationMode.HighQualityBicubic;
                    canvas.PixelOffsetMode = PixelOffsetMode.Half;
                    try
                    {
                        Parallel.For(0, total, new ParallelOptions {
                            MaxDegreeOfParallelism = 16, CancellationToken = stop.Token,
                        }, i =>
                        {
                            try
                            {
                                int col = i % grid.Cols, row = i / grid.Cols;
                                var url = source.BuildUrl(grid.X0 + col, grid.Y0 + row, grid.Zoom, key);
                                var bytes = TileHttp.Fetch(url, source.Referer, false, stop.Token);
                                using (var stream = new MemoryStream(bytes))
                                using (var tile = Image.FromStream(stream, false, true))
                                {
                                    var place = new Rectangle(col * TileGrid.Size - grid.OffsetX,
                                        row * TileGrid.Size - grid.OffsetY, TileGrid.Size, TileGrid.Size);
                                    lock (canvas)
                                        canvas.DrawImage(tile, place, 0, 0, tile.Width, tile.Height, GraphicsUnit.Pixel);
                                }
                            }
                            catch (TileHttp.RateLimited)
                            {
                                Interlocked.Exchange(ref limited, 1);
                                stop.Cancel();
                                return;
                            }
                            catch (Exception e)
                            {
                                if (!(e is IOException || e is ArgumentException || e is ExternalException)) throw;
                                Interlocked.Increment(ref failed);
                                Interlocked.Exchange(ref lastError, e.Message);
                            }
                            lock (canvas)
                            {
                                done++;
                                if (done == total || clock.ElapsedMilliseconds - lastUpdate >= 200)
                                {
                                    int n = Volatile.Read(ref failed);
                                    report(1 + 54.0 * done / total, "下载瓦片 " + done + "/" + total +
                                        (n == 0 ? "" : "，失败 " + n));
                                    lastUpdate = clock.ElapsedMilliseconds;
                                }
                            }
                        });
                    }
                    catch (OperationCanceledException)
                    {
                        if (limited != 0) throw new TileHttp.RateLimited();
                        throw;
                    }
                }
                if (failed == total)
                    throw new IOException("所有瓦片下载失败（" + lastError + "）。请检查图源、网络或天地图 Key");
                missing = failed;
                return image;
            }
            catch { image.Dispose(); throw; }
        }

        private static void Publish(string newJpg, string jpg, string newDxf, string dxf, string backup)
        {
            bool hadImage = File.Exists(jpg);
            if (hadImage) File.Replace(newJpg, jpg, backup);
            else File.Move(newJpg, jpg);
            try
            {
                if (File.Exists(dxf)) File.Replace(newDxf, dxf, null);
                else File.Move(newDxf, dxf);
            }
            catch
            {
                if (hadImage) File.Replace(backup, jpg, null);
                else File.Delete(jpg);
                throw;
            }
            RemoveTemporary(backup);
        }

        private static void RemoveTemporary(string path)
        {
            try { File.Delete(path); }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }
    }

    internal static class CadImage
    {
        private static string Number(double value) { return value.ToString("R", CultureInfo.InvariantCulture); }

        public static void Write(string path, string jpg, double[] bounds, Size size)
        {
            string template = Encoding.UTF8.GetString(Program.Asset("CadImage.dxf"));
            var values = new Dictionary<string, string> {
                { "LEFT", Number(bounds[0]) }, { "BOTTOM", Number(bounds[1]) },
                { "RIGHT", Number(bounds[2]) }, { "TOP", Number(bounds[3]) },
                { "WIDTH", Number(size.Width) }, { "HEIGHT", Number(size.Height) },
                { "CLIP_X", Number(size.Width - 0.5) }, { "CLIP_Y", Number(size.Height - 0.5) },
                { "PIXEL_X", Number((bounds[2] - bounds[0]) / size.Width) },
                { "PIXEL_Y", Number((bounds[3] - bounds[1]) / size.Height) },
                { "IMAGE_LABEL", Path.GetFileName(jpg) }, { "IMAGE_PATH", jpg },
                { "FINGERPRINTGUID", Guid.NewGuid().ToString("B").ToUpperInvariant() },
                { "VERSIONGUID", Guid.NewGuid().ToString("B").ToUpperInvariant() },
                { "DATE", Number(DateTime.UtcNow.ToOADate() + 2415018.5) },
            };
            string drawing = Regex.Replace(template, @"\{\{([A-Z_]+)\}\}", match => values[match.Groups[1].Value]);
            File.WriteAllText(path, drawing, new UTF8Encoding(false));
        }
    }
}
