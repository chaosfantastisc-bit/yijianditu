using System;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Reflection;
using System.Text;
using System.Threading;
using System.Web;
using System.Web.Script.Serialization;
using System.Windows.Forms;

[assembly: AssemblyTitle("一键下载卫星图到CAD")]
[assembly: AssemblyDescription("Windows 原生版：卫星影像转 CGCS2000 JPG + DXF")]
[assembly: AssemblyVersion("2.0.1.0")]
[assembly: AssemblyFileVersion("2.0.1.0")]
[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.8")]

namespace Yijianditu
{
    internal static class Program
    {
        public const string Version = "2.0.1";
        private static readonly object LogGate = new object();
        private static readonly Encoding Utf8 = new UTF8Encoding(false);

        [STAThread]
        private static int Main(string[] args)
        {
            bool noBrowser = args.Contains("--no-browser");
            try
            {
                int? port = null;
                for (int i = 0; i < args.Length; i++)
                {
                    if (args[i] == "--no-browser") continue;
                    int number;
                    if (args[i] != "--port" || ++i == args.Length ||
                        !int.TryParse(args[i], out number) || number < 0 || number > 65535)
                        throw new ArgumentException("可用参数：--no-browser、--port 端口号");
                    port = number;
                }
                int workers, completion;
                ThreadPool.GetMinThreads(out workers, out completion);
                ThreadPool.SetMinThreads(Math.Max(32, workers), completion);
                var server = LocalServer.Start(port);
                Log((server.Listener == null ? "打开已有服务 " : "启动服务 ") + server.Url);
                if (!noBrowser)
                {
                    try { Open(server.Url); }
                    catch (Exception e)
                    {
                        Log(e.Message);
                        MessageBox.Show("请用浏览器打开 " + server.Url, "一键地图");
                    }
                }
                server.Run();
                return 0;
            }
            catch (Exception e)
            {
                Log(e.ToString());
                if (!noBrowser) MessageBox.Show(e.Message, "一键地图启动失败");
                return 1;
            }
        }

        public static byte[] Asset(string name)
        {
            using (var input = Assembly.GetExecutingAssembly().GetManifestResourceStream(name))
            {
                if (input == null) throw new FileNotFoundException("缺少内嵌资源 " + name);
                using (var output = new MemoryStream())
                {
                    input.CopyTo(output);
                    return output.ToArray();
                }
            }
        }

        public static void Open(string target)
        {
            Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });
        }

        public static void Log(string message)
        {
            lock (LogGate)
            {
                try
                {
                    string directory = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "一键地图");
                    Directory.CreateDirectory(directory);
                    string file = Path.Combine(directory, "运行-原生版.log");
                    if (File.Exists(file) && new FileInfo(file).Length >= 512 * 1024)
                    {
                        File.Delete(file + ".1");
                        File.Move(file, file + ".1");
                    }
                    File.AppendAllText(file, DateTime.Now.ToString("yyyy-MM-dd HH:mm:ss ") + message + Environment.NewLine, Utf8);
                }
                catch (IOException) { }
                catch (UnauthorizedAccessException) { }
            }
        }
    }

    internal sealed class LocalServer
    {
        public readonly TcpListener Listener;
        private readonly int port;
        public string Url { get { return "http://127.0.0.1:" + port + "/"; } }
        private string Host { get { return "127.0.0.1:" + port; } }
        private readonly MapApi api = new MapApi();
        private readonly SemaphoreSlim connections = new SemaphoreSlim(48);
        private volatile bool stopped;

        private LocalServer(TcpListener listener, int port) { Listener = listener; this.port = port; }

        public static LocalServer Start(int? requested)
        {
            var candidates = requested.HasValue ? new[] { requested.Value } : Enumerable.Range(17840, 30);
            TcpListener available = null;
            foreach (int candidate in candidates)
            {
                var listener = new TcpListener(IPAddress.Loopback, candidate) { ExclusiveAddressUse = true };
                try
                {
                    listener.Start(48);
                    if (available == null) available = listener;
                    else listener.Stop();
                }
                catch (SocketException)
                {
                    listener.Stop();
                    if (IsRunning(candidate))
                    {
                        if (available != null) available.Stop();
                        return new LocalServer(null, candidate);
                    }
                }
            }
            if (available != null) return new LocalServer(available, ((IPEndPoint)available.LocalEndpoint).Port);
            throw new IOException("找不到可用的本机端口");
        }

        private static bool IsRunning(int port)
        {
            var request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + port + "/api/ping");
            request.Proxy = null;
            request.Timeout = request.ReadWriteTimeout = 800;
            try
            {
                using (var response = request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream()))
                    return reader.ReadToEnd() == "{\"app\":\"yijianditu-native\",\"version\":\"" + Program.Version + "\"}";
            }
            catch (WebException) { return false; }
            catch (IOException) { return false; }
        }

        public void Run()
        {
            if (Listener == null) return;
            while (!stopped)
            {
                TcpClient client;
                try { client = Listener.AcceptTcpClient(); }
                catch (SocketException) { if (stopped) return; throw; }
                catch (InvalidOperationException) { if (stopped) return; throw; }
                if (!connections.Wait(0))
                {
                    client.Close();
                    continue;
                }
                ThreadPool.QueueUserWorkItem(_ =>
                {
                    try { Serve(client); }
                    finally { connections.Release(); }
                });
            }
        }

        private void Serve(TcpClient client)
        {
            using (client)
            {
                client.ReceiveTimeout = client.SendTimeout = 6000;
                client.NoDelay = true;
                try
                {
                    var stream = client.GetStream();
                    Response response;
                    try
                    {
                        var request = Request.Read(new BufferedStream(stream, 4096), Host, Url.TrimEnd('/'));
                        if (request == null) return;
                        response = api.Route(request);
                    }
                    catch (HttpError e) { response = Response.Json(new { error = e.Message }, e.Status); }
                    catch (ArgumentException e) { response = Response.Json(new { error = e.Message, out_of_range = true }, 400); }
                    catch (IOException) { return; }
                    catch (Exception e)
                    {
                        Program.Log(e.ToString());
                        response = Response.Json(new { error = "本地服务处理失败" }, 500);
                    }
                    try { response.Send(stream); }
                    finally
                    {
                        if (response.Exit)
                        {
                            stopped = true;
                            Listener.Stop();
                        }
                    }
                }
                catch (IOException) { }
                catch (SocketException) { }
                catch (ObjectDisposedException) { }
            }
        }
    }

    internal sealed class HttpError : Exception
    {
        public readonly int Status;
        public HttpError(int status, string message) : base(message) { Status = status; }
    }

    internal sealed class Request
    {
        private const int BodyLimit = 16384;
        public readonly string Method, Path;
        public readonly NameValueCollection Query;
        public readonly Dictionary<string, object> Body;

        private Request(string method, string path, NameValueCollection query, Dictionary<string, object> body)
        {
            Method = method; Path = path; Query = query; Body = body;
        }

        public static Request Read(Stream stream, string host, string origin)
        {
            int headerBytes = 0;
            string line = ReadLine(stream, ref headerBytes);
            if (line == null || line.Length == 0) return null;
            var request = line.Split(' ');
            if (request.Length != 3 || !request[1].StartsWith("/") ||
                (request[2] != "HTTP/1.0" && request[2] != "HTTP/1.1"))
                throw new HttpError(400, "请求格式错误");
            if (request[0] != "GET" && request[0] != "POST")
                throw new HttpError(405, "请求方法不支持");
            var headers = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            while (!string.IsNullOrEmpty(line = ReadLine(stream, ref headerBytes)))
            {
                int colon = line.IndexOf(':');
                if (colon < 1) throw new HttpError(400, "请求头无效");
                string name = line.Substring(0, colon);
                if (headers.ContainsKey(name)) throw new HttpError(400, "请求头重复");
                headers[name] = line.Substring(colon + 1).Trim();
            }
            string value;
            if (!headers.TryGetValue("Host", out value) || !value.Equals(host, StringComparison.OrdinalIgnoreCase) ||
                (headers.TryGetValue("Origin", out value) && value != origin) ||
                (headers.TryGetValue("Sec-Fetch-Site", out value) && value == "cross-site"))
                throw new HttpError(403, "仅允许从本机页面访问");
            var raw = request[1].Split(new[] { '?' }, 2);
            var query = HttpUtility.ParseQueryString(raw.Length == 2 ? raw[1] : "");
            Dictionary<string, object> body = null;
            if (request[0] == "POST")
            {
                if (!headers.TryGetValue("Content-Type", out value) ||
                    !value.Split(';')[0].Trim().Equals("application/json", StringComparison.OrdinalIgnoreCase))
                    throw new HttpError(415, "需要 JSON 请求");
                int count;
                if (!headers.TryGetValue("Content-Length", out value) || !int.TryParse(value, out count) ||
                    count <= 0 || count > BodyLimit || headers.ContainsKey("Transfer-Encoding"))
                    throw new HttpError(400, "请求体长度无效");
                var bytes = new byte[count];
                for (int offset = 0; offset < bytes.Length; )
                {
                    int read = stream.Read(bytes, offset, bytes.Length - offset);
                    if (read == 0) throw new HttpError(400, "请求体不完整");
                    offset += read;
                }
                try { body = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(Encoding.UTF8.GetString(bytes)); }
                catch (InvalidOperationException) { throw new HttpError(400, "JSON 格式无效"); }
                if (body == null) throw new HttpError(400, "JSON 请求无效");
            }
            return new Request(request[0], raw[0], query, body);
        }

        private static string ReadLine(Stream stream, ref int bytes)
        {
            var chars = new StringBuilder();
            int value;
            while ((value = stream.ReadByte()) >= 0)
            {
                if (++bytes > 16384) throw new HttpError(400, "请求头过长");
                if (value == '\n') return chars.ToString().TrimEnd('\r');
                chars.Append((char)value);
            }
            return null;
        }
    }

    internal sealed class Response
    {
        private readonly byte[] body;
        private readonly int status;
        private readonly string type, cache;
        public bool Exit;

        public Response(byte[] body, string type, int status = 200, string cache = "no-store")
        {
            this.body = body; this.type = type; this.status = status; this.cache = cache;
        }

        public static Response Json(object data, int status = 200)
        {
            var text = new JavaScriptSerializer().Serialize(data);
            return new Response(Encoding.UTF8.GetBytes(text), "application/json; charset=utf-8", status);
        }

        public void Send(Stream stream)
        {
            var reasons = new Dictionary<int, string> {
                {200, "OK"}, {400, "Bad Request"}, {403, "Forbidden"}, {404, "Not Found"},
                {405, "Method Not Allowed"}, {409, "Conflict"}, {415, "Unsupported Media Type"},
                {500, "Internal Server Error"}, {502, "Bad Gateway"}, {503, "Service Unavailable"},
            };
            byte[] header = Encoding.ASCII.GetBytes("HTTP/1.1 " + status + " " + reasons[status] + "\r\n" +
                "Content-Type: " + type + "\r\nContent-Length: " + body.Length + "\r\nCache-Control: " + cache +
                "\r\nX-Content-Type-Options: nosniff\r\nConnection: close\r\n\r\n");
            stream.Write(header, 0, header.Length);
            stream.Write(body, 0, body.Length);
        }
    }
}
