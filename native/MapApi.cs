using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Threading;

namespace Yijianditu
{
    internal sealed class MapApi
    {
        private static readonly string PublicToken = Environment.GetEnvironmentVariable("TIANDITU_PUBLIC_TOKEN")
            ?? "436ce7e50d27eede2f2929307e6b33c0";
        private static readonly string TokenFile = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".yijianditu_token");
        private static readonly string OutputDirectory = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "一键地图输出");
        private static readonly Dictionary<string, Response> Pages = OpenPages();
        private readonly object gate = new object();
        private readonly List<DownloadTask> tasks = new List<DownloadTask>();
        private bool closing;
        private string currentToken = LoadToken();

        private static Dictionary<string, Response> OpenPages()
        {
            var result = new Dictionary<string, Response>();
            result["/"] = result["/index.html"] = Page("index.html", "text/html; charset=utf-8");
            result["/static/app.js"] = Page("app.js", "application/javascript; charset=utf-8");
            result["/static/app.css"] = Page("app.css", "text/css; charset=utf-8");
            result["/static/vendor/leaflet.js"] = Page("vendor/leaflet.js", "application/javascript; charset=utf-8");
            result["/static/vendor/leaflet.css"] = Page("vendor/leaflet.css", "text/css; charset=utf-8");
            result["/static/logo.svg"] = Page("logo.svg", "image/svg+xml");
            return result;
        }

        private static Response Page(string name, string type)
        {
            return new Response(Program.Asset(name), type);
        }

        public Response Route(Request request)
        {
            if (request.Method == "GET")
            {
                Response page;
                if (Pages.TryGetValue(request.Path, out page)) return page;
                switch (request.Path)
                {
                    case "/api/ping": return Response.Json(new { app = "yijianditu-native", version = Program.Version });
                    case "/api/config": return Config();
                    case "/api/task": return TaskStatus(request.Query["id"]);
                    case "/api/tile": return Preview(request);
                }
            }
            else
            {
                var body = request.Body;
                switch (request.Path)
                {
                    case "/api/estimate": return Estimate(body);
                    case "/api/download": return StartDownload(body);
                    case "/api/cancel": return Cancel(Text(body, "id"));
                    case "/api/open": return OpenFolder(Text(body, "path"));
                    case "/api/exit": return Exit();
                }
            }
            throw new HttpError(404, "页面不存在");
        }

        private Response Config()
        {
            string key;
            lock (gate) key = currentToken;
            return Response.Json(new {
                version = Program.Version, sources = TileSource.All.Select(s => s.Describe()).ToArray(),
                default_source = TileSource.ArcGis.Id, default_zoom = 17, output_dir = OutputDirectory,
                default_target = "zone:39", crs_default_mode = "zone", crs_manual_preset = 118.5,
                crs_zone_range = new[] {25, 45}, crs_cm_range = new[] {75, 135},
                crs_note = "下载范围横跨多个度带时，以小编号（最西侧带）为准",
                tianditu_token = key, tianditu_public_token = PublicToken,
                limits = new { max_tiles = TileGrid.MaxTiles, max_pixels = TileGrid.MaxPixels, jpg_max_mb = Raster.JpegMaxMb },
            });
        }

        private Response Preview(Request request)
        {
            var source = TileSource.Find(request.Query["src"]);
            int x, y, z;
            if (!int.TryParse(request.Query["x"], out x) || !int.TryParse(request.Query["y"], out y) ||
                !int.TryParse(request.Query["z"], out z) || z < 1 || z > source.MaxZoom ||
                x < 0 || y < 0 || x >= (1 << z) || y >= (1 << z))
                throw new HttpError(400, "瓦片坐标无效");
            bool label = request.Query["layer"] == "label";
            if (label && source.LabelUrl == null) throw new HttpError(404, "没有注记图层");
            string key;
            lock (gate) key = currentToken;
            try
            {
                var data = TileHttp.Fetch(source.BuildUrl(x, y, z, key, label), source.Referer, true, CancellationToken.None);
                return new Response(data, TileHttp.ContentType(data), 200, "max-age=86400");
            }
            catch (IOException) { throw new HttpError(502, "图源暂时不可用"); }
        }

        private static Response Estimate(Dictionary<string, object> body)
        {
            var options = new DownloadOptions(body);
            var grid = options.Grid;
            string limit = grid.LimitError();
            OutputGrid output = null;
            if (limit == null)
            {
                try { output = new OutputGrid(grid, options.Target); }
                catch (ArgumentException e) { limit = e.Message; }
            }
            var ll = options.Target.Forward(options.MinLon, options.MinLat);
            var ur = options.Target.Forward(options.MaxLon, options.MaxLat);
            long destPixels = output == null ? (long)grid.Width * grid.Height : (long)output.Width * output.Height;
            return Response.Json(new {
                zoom = grid.Zoom, tile_count = grid.TileCount, cols = grid.Cols, rows = grid.Rows,
                width = grid.Width, height = grid.Height, pixel_count = (long)grid.Width * grid.Height,
                ground_resolution = Math.Round(grid.Resolution *
                    Math.Cos((options.MinLat + options.MaxLat) / 2 * Mercator.Degrees), 4),
                est_download_mb = Math.Round(grid.TileCount * 22 / 1024.0, 1),
                est_memory_mb = Math.Round(((long)grid.Width * grid.Height + destPixels) * 3 / 1048576.0, 1),
                limit_error = limit, target_id = options.Target.Id, target_label = options.Target.Label,
                target_ll = output == null ? null : new[] {Math.Round(ll.X, 3), Math.Round(ll.Y, 3)},
                target_ur = output == null ? null : new[] {Math.Round(ur.X, 3), Math.Round(ur.Y, 3)},
                zone = options.Target.Zone, meridian = 3 * options.Target.Zone,
            });
        }

        private Response StartDownload(Dictionary<string, object> body)
        {
            var options = new DownloadOptions(body);
            string limit = options.Grid.LimitError();
            if (limit != null) throw new HttpError(400, limit);
            var output = new OutputGrid(options.Grid, options.Target);
            string directory = Text(body, "output_dir");
            if (directory.Length == 0) directory = OutputDirectory;
            string name = Text(body, "name");
            string key;
            DownloadTask task;
            lock (gate)
            {
                if (closing) throw new HttpError(503, "程序正在退出");
                var running = tasks.LastOrDefault();
                if (running != null && running.Status == "running")
                    return Response.Json(new {task_id = running.Id, already_running = true}, 409);
                key = currentToken;
                if (options.Source == TileSource.TianDiTu && body.ContainsKey("tianditu_token"))
                {
                    string value = Text(body, "tianditu_token");
                    key = currentToken = value.Length == 0 ? PublicToken : value;
                    SaveToken(value);
                }
                task = new DownloadTask(gate);
                tasks.Add(task);
                if (tasks.Count > 10)
                {
                    tasks[0].Cancel.Dispose();
                    tasks.RemoveAt(0);
                }
            }
            var worker = new Thread(() =>
            {
                try
                {
                    var result = MapJob.Run(options.Grid, output, options.Target, options.Source,
                        directory, name, key, task.Cancel.Token, (progress, message) => task.Update(progress, message));
                    task.Update(100, "全部完成", "done", result);
                }
                catch (OperationCanceledException)
                {
                    task.Update(0, "任务已取消", "cancelled", new {success = false, error = "已取消"});
                }
                catch (Exception e)
                {
                    Program.Log(e.ToString());
                    string error = e is OutOfMemoryException ? "内存不足，请降低级别或缩小范围" : e.Message;
                    task.Update(0, "失败：" + error, "error", new {success = false, error});
                }
            }) { IsBackground = true, Name = "Satellite Map Download" };
            try { worker.Start(); }
            catch (Exception e)
            {
                Program.Log(e.ToString());
                task.Update(0, "无法创建下载线程", "error");
            }
            return Response.Json(new {task_id = task.Id});
        }

        private Response TaskStatus(string id)
        {
            lock (gate)
            {
                var task = string.IsNullOrEmpty(id) ? tasks.LastOrDefault() : tasks.Find(job => job.Id == id);
                if (task != null) return Response.Json(task.View());
                if (string.IsNullOrEmpty(id)) return Response.Json(null);
            }
            throw new HttpError(404, "任务不存在");
        }

        private Response Cancel(string id)
        {
            DownloadTask task;
            lock (gate) task = tasks.Find(job => job.Id == id && job.Status == "running");
            if (task == null) return Response.Json(new {ok = false});
            try { task.Cancel.Cancel(); }
            catch (ObjectDisposedException) { }
            return Response.Json(new {ok = true});
        }

        private static Response OpenFolder(string path)
        {
            try
            {
                string folder = Directory.Exists(path) ? path : Path.GetDirectoryName(path);
                if (!Directory.Exists(folder)) throw new HttpError(400, "目录不存在");
                Program.Open(folder);
                return Response.Json(new {ok = true});
            }
            catch (System.ComponentModel.Win32Exception e) { throw new HttpError(400, e.Message); }
        }

        private Response Exit()
        {
            lock (gate)
            {
                var last = tasks.LastOrDefault();
                if (last != null && last.Status == "running")
                    throw new HttpError(409, "下载任务仍在运行，请等待完成或先取消");
                closing = true;
            }
            var response = Response.Json(new {ok = true});
            response.Exit = true;
            return response;
        }

        private static string LoadToken()
        {
            try
            {
                if (File.Exists(TokenFile))
                {
                    var saved = File.ReadAllText(TokenFile).Trim();
                    if (saved.Length != 0) return saved;
                }
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
            return Environment.GetEnvironmentVariable("TIANDITU_TOKEN") ?? PublicToken;
        }

        private static void SaveToken(string key)
        {
            try
            {
                if (key.Length == 0) File.Delete(TokenFile);
                else File.WriteAllText(TokenFile, key);
            }
            catch (IOException) { }
            catch (UnauthorizedAccessException) { }
        }

        private static string Text(Dictionary<string, object> body, string name, string fallback = "")
        {
            object value;
            return body.TryGetValue(name, out value) && value != null
                ? Convert.ToString(value, CultureInfo.InvariantCulture).Trim() : fallback;
        }

        private static double Number(Dictionary<string, object> body, string name)
        {
            double value;
            if (!double.TryParse(Text(body, name), NumberStyles.Float, CultureInfo.InvariantCulture, out value) ||
                double.IsNaN(value) || double.IsInfinity(value))
                throw new ArgumentException("参数无效：" + name);
            return value;
        }

        private sealed class DownloadOptions
        {
            public readonly double MinLon, MinLat, MaxLon, MaxLat;
            public readonly TileSource Source;
            public readonly Target Target;
            public readonly TileGrid Grid;

            public DownloadOptions(Dictionary<string, object> body)
            {
                Source = TileSource.Find(Text(body, "source", TileSource.ArcGis.Id));
                double zoom = Number(body, "zoom");
                if (zoom != Math.Floor(zoom) || zoom < 1 || zoom > Source.MaxZoom)
                    throw new ArgumentException(Source.Name + " 只支持 1～" + Source.MaxZoom + " 级");
                MinLon = Number(body, "min_lon");
                MaxLon = Number(body, "max_lon");
                MinLat = Number(body, "min_lat");
                MaxLat = Number(body, "max_lat");
                string mode = Text(body, "crs_mode", "zone");
                Target = Target.Create(MinLon, MaxLon, mode, mode == "manual" ? Number(body, "manual_meridian") : 0);
                Grid = new TileGrid(MinLon, MinLat, MaxLon, MaxLat, (int)zoom);
            }
        }

        private sealed class DownloadTask
        {
            public readonly string Id = Guid.NewGuid().ToString("N").Substring(0, 12);
            public readonly CancellationTokenSource Cancel = new CancellationTokenSource();
            private readonly object gate;
            private readonly Queue<string> logs = new Queue<string>();
            private string message = "任务已创建";
            private double progress;
            private object result;
            public string Status { get; private set; }

            public DownloadTask(object gate) { this.gate = gate; Status = "running"; }

            public void Update(double percent, string message, string status = "running", object result = null)
            {
                lock (gate)
                {
                    progress = Math.Round(percent, 1);
                    this.message = message;
                    Status = status;
                    this.result = result;
                    logs.Enqueue(message);
                    if (logs.Count > 40) logs.Dequeue();
                }
            }

            public object View()
            {
                return new {id = Id, status = Status, progress, message, logs = logs.ToArray(), result};
            }
        }
    }
}
