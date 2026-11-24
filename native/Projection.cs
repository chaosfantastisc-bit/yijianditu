using System;
using System.Globalization;

namespace Yijianditu
{
    internal struct Point2
    {
        public readonly double X, Y;
        public Point2(double x, double y) { X = x; Y = y; }
    }

    internal static class Mercator
    {
        public const double Radius = 6378137.0;
        public const double Origin = Math.PI * Radius;
        public const double Degrees = Math.PI / 180.0;
        public const double MaxLatitude = 85.05112877980659;

        public static Point2 Forward(double lon, double lat)
        {
            lat = Math.Max(-MaxLatitude, Math.Min(MaxLatitude, lat));
            return new Point2(lon * Degrees * Radius,
                Math.Log(Math.Tan(Math.PI / 4 + lat * Degrees / 2)) * Radius);
        }

        public static Point2 Inverse(double x, double y)
        {
            return new Point2(x / Origin * 180, Math.Atan(Math.Sinh(y / Radius)) / Degrees);
        }
    }

    internal sealed class TileGrid
    {
        public const int Size = 256;
        public const int MaxTiles = 12000;
        public const long MaxPixels = 260000000;
        public readonly int Zoom, Width, Height, X0, Y0, X1, Y1, OffsetX, OffsetY;
        public readonly double Left, Top, Resolution;
        public int Cols { get { return X1 - X0 + 1; } }
        public int Rows { get { return Y1 - Y0 + 1; } }
        public long TileCount { get { return (long)Cols * Rows; } }
        public long MosaicPixels { get { return TileCount * Size * Size; } }

        public TileGrid(double lon0, double lat0, double lon1, double lat1, int zoom)
        {
            if (!(lon0 < lon1 && lat0 < lat1 && lat0 >= -Mercator.MaxLatitude &&
                lat1 <= Mercator.MaxLatitude && lon0 >= -180 && lon1 <= 180))
                throw new ArgumentException("范围无效，请检查经纬度");
            Zoom = zoom;
            int side = 1 << zoom;
            Resolution = 2 * Mercator.Origin / (Size * side);
            var ll = Mercator.Forward(lon0, lat0);
            var ur = Mercator.Forward(lon1, lat1);
            double left = (ll.X + Mercator.Origin) / Resolution;
            double top = (Mercator.Origin - ur.Y) / Resolution;
            Width = Math.Max(1, (int)Math.Round((ur.X + Mercator.Origin) / Resolution - left));
            Height = Math.Max(1, (int)Math.Round((Mercator.Origin - ll.Y) / Resolution - top));
            int px = (int)Math.Floor(left);
            int py = (int)Math.Floor(top);
            X0 = Math.Max(0, Math.Min(side - 1, px / Size));
            Y0 = Math.Max(0, Math.Min(side - 1, py / Size));
            X1 = Math.Max(X0, Math.Min(side - 1, (px + Width - 1) / Size));
            Y1 = Math.Max(Y0, Math.Min(side - 1, (py + Height - 1) / Size));
            OffsetX = px - X0 * Size;
            OffsetY = py - Y0 * Size;
            Left = px * Resolution - Mercator.Origin;
            Top = Mercator.Origin - py * Resolution;
        }

        public string LimitError()
        {
            if (TileCount > MaxTiles)
                return "选区过大，需要 " + TileCount + " 张瓦片，请降低级别或缩小范围";
            if (MosaicPixels > MaxPixels)
                return "选区过大，拼接后超过 260 百万像素，请降低级别或缩小范围";
            return null;
        }
    }

    // Sixth-order Krüger series for the GRS80 ellipsoid. Formula reference:
    // https://proj.org/en/stable/operations/projections/tmerc.html
    internal sealed class Target
    {
        private const double F = 1 / 298.257222101;
        private const double N = F / (2 - F);
        private const double N2 = N * N, N3 = N2 * N, N4 = N3 * N, N5 = N4 * N, N6 = N5 * N;
        private const double A = Mercator.Radius / (1 + N) * (1 + N2 / 4 + N4 / 64 + N6 / 256);
        private static readonly double E = Math.Sqrt(F * (2 - F));
        private static readonly double[] Alpha = {
            N / 2 - 2 * N2 / 3 + 5 * N3 / 16 + 41 * N4 / 180 - 127 * N5 / 288 + 7891 * N6 / 37800,
            13 * N2 / 48 - 3 * N3 / 5 + 557 * N4 / 1440 + 281 * N5 / 630 - 1983433 * N6 / 1935360,
            61 * N3 / 240 - 103 * N4 / 140 + 15061 * N5 / 26880 + 167603 * N6 / 181440,
            49561 * N4 / 161280 - 179 * N5 / 168 + 6601661 * N6 / 7257600,
            34729 * N5 / 80640 - 3418889 * N6 / 1995840,
            212378941 * N6 / 319334400,
        };
        private static readonly double[] Beta = {
            N / 2 - 2 * N2 / 3 + 37 * N3 / 96 - N4 / 360 - 81 * N5 / 512 + 96199 * N6 / 604800,
            N2 / 48 + N3 / 15 - 437 * N4 / 1440 + 46 * N5 / 105 - 1118711 * N6 / 3870720,
            17 * N3 / 480 - 37 * N4 / 840 - 209 * N5 / 4480 + 5569 * N6 / 90720,
            4397 * N4 / 161280 - 11 * N5 / 504 - 830251 * N6 / 7257600,
            4583 * N5 / 161280 - 108847 * N6 / 3991680,
            20648693 * N6 / 638668800,
        };

        public readonly int Zone;
        public readonly double Meridian;
        public readonly string Label, Id;
        private readonly double easting;

        private Target(int zone, double meridian, string mode)
        {
            Zone = zone;
            Meridian = meridian;
            easting = 500000 + (mode == "zone" ? zone * 1000000 : 0);
            if (mode == "zone")
            {
                Id = "zone:" + zone;
                Label = "CGCS2000_3_Degree_GK_Zone_" + zone + "（中央经线 " + 3 * zone + "°E，含带号）";
            }
            else
            {
                var number = meridian.ToString(mode == "manual" ? "F4" : "0", CultureInfo.InvariantCulture);
                Id = mode + ":" + meridian.ToString(CultureInfo.InvariantCulture);
                Label = "CGCS2000_3_Degree_GK_CM_" + number + "E" +
                    (mode == "manual" ? "（手动中央经线）" : "（中央经线 " + number + "°E）");
            }
        }

        public static Target Create(double minLon, double maxLon, string mode, double manual)
        {
            if (!(minLon >= 73.5 && maxLon <= 136.5 && minLon < maxLon))
                throw new ArgumentException("选择区域超出范围（仅支持经度 73.5°E～136.5°E）");
            int zone = Math.Min(45, (int)Math.Floor((minLon + 1.5) / 3));
            if (mode == "zone" || mode == "cm") return new Target(zone, 3 * zone, mode);
            if (mode != "manual" || !(manual > 0 && manual < 180))
                throw new ArgumentException("手动中央经线应在 0～180°E 之间");
            return new Target(zone, manual, mode);
        }

        private static double Asinh(double t)
        {
            double a = Math.Abs(t);
            return (t < 0 ? -1 : 1) * Math.Log(a + Math.Sqrt(1 + a * a));
        }

        private static double Atanh(double t) { return 0.5 * Math.Log((1 + t) / (1 - t)); }

        public Point2 Forward(double longitude, double latitude)
        {
            double phi = latitude * Mercator.Degrees, lambda = (longitude - Meridian) * Mercator.Degrees;
            double t = Math.Sinh(Asinh(Math.Tan(phi)) - E * Atanh(E * Math.Sin(phi)));
            double c = Math.Cos(lambda);
            double xi = Math.Atan2(t, c);
            double eta = Asinh(Math.Sin(lambda) / Math.Sqrt(t * t + c * c));
            double north = xi, east = eta;
            for (int j = 1; j <= Alpha.Length; j++)
            {
                north += Alpha[j - 1] * Math.Sin(2 * j * xi) * Math.Cosh(2 * j * eta);
                east += Alpha[j - 1] * Math.Cos(2 * j * xi) * Math.Sinh(2 * j * eta);
            }
            return new Point2(easting + A * east, A * north);
        }

        public Point2 Inverse(double x, double y)
        {
            double xi = y / A, eta = (x - easting) / A, north = xi, east = eta;
            for (int j = 1; j <= Beta.Length; j++)
            {
                north -= Beta[j - 1] * Math.Sin(2 * j * xi) * Math.Cosh(2 * j * eta);
                east -= Beta[j - 1] * Math.Cos(2 * j * xi) * Math.Sinh(2 * j * eta);
            }
            double s = Math.Sinh(east), c = Math.Cos(north);
            double q0 = Asinh(Math.Sin(north) / Math.Sqrt(s * s + c * c));
            double q = q0;
            for (int i = 0; i < 8; i++)
                q = q0 + E * Atanh(E * Math.Tanh(q));
            return new Point2(Meridian + Math.Atan2(s, c) / Mercator.Degrees,
                Math.Atan(Math.Sinh(q)) / Mercator.Degrees);
        }
    }

    internal sealed class OutputGrid
    {
        public const int MaxJpegSide = 65500;
        public readonly int Width, Height;
        public readonly double Left, Top, Resolution;
        public double[] Bounds
        {
            get
            {
                return new[] { Math.Round(Left, 4), Math.Round(Top - Height * Resolution, 4),
                    Math.Round(Left + Width * Resolution, 4), Math.Round(Top, 4) };
            }
        }

        public OutputGrid(TileGrid input, Target target)
        {
            // Sample the footprint boundary and preserve the resolution along its diagonal,
            // as in GDALSuggestedWarpOutput for a continuous projected extent.
            int steps = Math.Max(20, Math.Min(100, (int)(Math.Min(input.Width, input.Height) / 50.0 + 0.5)));
            double minX = double.PositiveInfinity, maxX = double.NegativeInfinity;
            double minY = double.PositiveInfinity, maxY = double.NegativeInfinity;
            Func<double, double, Point2> project = (x, y) =>
            {
                var soil = Mercator.Inverse(input.Left + x * input.Resolution, input.Top - y * input.Resolution);
                var p = target.Forward(soil.X, soil.Y);
                var inverse = target.Inverse(p.X, p.Y);
                if (!(Math.Abs(inverse.X - soil.X) < 1e-6 && Math.Abs(inverse.Y - soil.Y) < 1e-6))
                    throw new ArgumentException("所选范围与中央经线相距过远，请选用相邻的中央经线");
                minX = Math.Min(minX, p.X);
                maxX = Math.Max(maxX, p.X);
                minY = Math.Min(minY, p.Y);
                maxY = Math.Max(maxY, p.Y);
                return p;
            };
            var start = project(0, 0);
            var end = project(input.Width, input.Height);
            for (int i = 0; i <= steps; i++)
            {
                double t = i / (double)steps;
                project(t * input.Width, 0);
                project(t * input.Width, input.Height);
                project(0, t * input.Height);
                project(input.Width, t * input.Height);
            }
            double dx = end.X - start.X, dy = end.Y - start.Y;
            if (dx == 0 || dy == 0) { dx = maxX - minX; dy = maxY - minY; }
            Resolution = Math.Sqrt((dx * dx + dy * dy) /
                ((double)input.Width * input.Width + (double)input.Height * input.Height));
            double width = (maxX - minX) / Resolution, height = (maxY - minY) / Resolution;
            if (!(width > 0 && height > 0 && width < MaxJpegSide && height < MaxJpegSide &&
                width * height <= TileGrid.MaxPixels))
                throw new ArgumentException("重投影后的影像过大，请降低级别或缩小范围");
            Width = Math.Max(1, (int)(width + 0.5));
            Height = Math.Max(1, (int)(height + 0.5));
            Left = minX;
            Top = maxY;
        }
    }
}
