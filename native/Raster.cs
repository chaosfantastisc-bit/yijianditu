using System;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.IO;
using System.Linq;
using System.Threading;
using System.Threading.Tasks;

namespace Yijianditu
{
    internal static class Raster
    {
        private const int FractionSteps = 1024;
        public const int JpegMaxMb = 30;
        private static readonly CubicWeights[] Weights = Enumerable.Range(0, FractionSteps)
            .Select(i => new CubicWeights(i / (double)FractionSteps)).ToArray();
        private static readonly ImageCodecInfo JpegCodec = ImageCodecInfo.GetImageEncoders()
            .First(codec => codec.FormatID == ImageFormat.Jpeg.Guid);

        private struct CubicWeights
        {
            public readonly double A, B, C, D;
            public CubicWeights(double t)
            {
                double t2 = t * t, t3 = t2 * t;
                A = -0.5 * t + t2 - 0.5 * t3;
                B = 1 - 2.5 * t2 + 1.5 * t3;
                C = 0.5 * t + 2 * t2 - 1.5 * t3;
                D = -0.5 * t2 + 0.5 * t3;
            }
        }

        public static Bitmap Warp(Bitmap source, TileGrid grid, OutputGrid output, Target target,
            CancellationToken token)
        {
            var image = new Bitmap(output.Width, output.Height, PixelFormat.Format24bppRgb);
            var src = source.LockBits(new Rectangle(0, 0, source.Width, source.Height),
                ImageLockMode.ReadOnly, PixelFormat.Format24bppRgb);
            try
            {
                var dst = image.LockBits(new Rectangle(0, 0, image.Width, image.Height),
                    ImageLockMode.WriteOnly, PixelFormat.Format24bppRgb);
                try
                {
                    Parallel.For(0, output.Height, new ParallelOptions {
                        MaxDegreeOfParallelism = Math.Min(Environment.ProcessorCount, 6), CancellationToken = token,
                    }, row => WarpRow(row, src, dst, grid, output, target));
                }
                finally { image.UnlockBits(dst); }
                return image;
            }
            catch { image.Dispose(); throw; }
            finally { source.UnlockBits(src); }
        }

        private static unsafe void WarpRow(int row, BitmapData source, BitmapData destination,
            TileGrid grid, OutputGrid output, Target target)
        {
            double north = output.Top - (row + 0.5) * output.Resolution;
            byte* pixel = (byte*)destination.Scan0 + row * destination.Stride;
            for (int start = 0; start < output.Width; start += 64)
            {
                int end = Math.Min(start + 64, output.Width);
                var a = SourcePixel(start, north, grid, output, target);
                var b = SourcePixel(end, north, grid, output, target);
                WarpSegment(start, end, north, a, b, pixel, source, grid, output, target);
            }
        }

        private static Point2 SourcePixel(double column, double north, TileGrid grid, OutputGrid output, Target target)
        {
            var lonLat = target.Inverse(output.Left + (column + 0.5) * output.Resolution, north);
            var metres = Mercator.Forward(lonLat.X, lonLat.Y);
            return new Point2((metres.X - grid.Left) / grid.Resolution - 0.5,
                (grid.Top - metres.Y) / grid.Resolution - 0.5);
        }

        private static unsafe void WarpSegment(int start, int end, double north, Point2 a, Point2 b,
            byte* row, BitmapData source, TileGrid grid, OutputGrid output, Target target)
        {
            int length = end - start;
            double dx = (b.X - a.X) / length, dy = (b.Y - a.Y) / length;
            if (length > 1)
            {
                int mid = start + length / 2;
                var actual = SourcePixel(mid, north, grid, output, target);
                double error = Math.Abs(actual.X - (a.X + dx * (mid - start))) +
                    Math.Abs(actual.Y - (a.Y + dy * (mid - start)));
                if (!(error < 0.02))
                {
                    WarpSegment(start, mid, north, a, actual, row, source, grid, output, target);
                    WarpSegment(mid, end, north, actual, b, row, source, grid, output, target);
                    return;
                }
            }
            for (int x = start; x < end; x++)
                Sample(source, a.X + (x - start) * dx, a.Y + (x - start) * dy, row + 3 * x);
        }

        private static int Clamp(int i, int size) { return Math.Max(0, Math.Min(i, size - 1)); }

        private static unsafe void Sample(BitmapData source, double x, double y, byte* dest)
        {
            if (!(x >= -0.5 && y >= -0.5 && x < source.Width - 0.5 && y < source.Height - 0.5))
            {
                dest[0] = dest[1] = dest[2] = 0;
                return;
            }
            int ix = (int)Math.Floor(x), iy = (int)Math.Floor(y);
            var wx = Weights[(int)((x - ix) * FractionSteps)];
            var wy = Weights[(int)((y - iy) * FractionSteps)];
            int x0 = Clamp(ix - 1, source.Width) * 3;
            int x1 = Clamp(ix, source.Width) * 3;
            int x2 = Clamp(ix + 1, source.Width) * 3;
            int x3 = Clamp(ix + 2, source.Width) * 3;
            byte* origin = (byte*)source.Scan0;
            byte* r0 = origin + Clamp(iy - 1, source.Height) * source.Stride;
            byte* r1 = origin + Clamp(iy, source.Height) * source.Stride;
            byte* r2 = origin + Clamp(iy + 1, source.Height) * source.Stride;
            byte* r3 = origin + Clamp(iy + 2, source.Height) * source.Stride;
            for (int c = 0; c < 3; c++)
            {
                double v = wy.A * Along(r0 + c, x0, x1, x2, x3, wx) +
                    wy.B * Along(r1 + c, x0, x1, x2, x3, wx) +
                    wy.C * Along(r2 + c, x0, x1, x2, x3, wx) +
                    wy.D * Along(r3 + c, x0, x1, x2, x3, wx);
                dest[c] = (byte)Math.Min(255, Math.Max(0, (int)(v + 0.5)));
            }
        }

        private static unsafe double Along(byte* row, int a, int b, int c, int d, CubicWeights w)
        {
            return row[a] * w.A + row[b] * w.B + row[c] * w.C + row[d] * w.D;
        }

        public static Size SaveJpeg(Bitmap image, string path, CancellationToken token)
        {
            const long limit = JpegMaxMb * 1024L * 1024L;
            Bitmap working = image;
            try
            {
                while (true)
                {
                    if (Encode(working, path, 95, token) <= limit) return working.Size;
                    long size = Encode(working, path, 10, token);
                    if (size <= limit)
                    {
                        string candidate = path + ".candidate";
                        try
                        {
                            int low = 11, high = 94;
                            while (low <= high)
                            {
                                int quality = (low + high) / 2;
                                if (Encode(working, candidate, quality, token) <= limit)
                                {
                                    File.Replace(candidate, path, null);
                                    low = quality + 1;
                                }
                                else high = quality - 1;
                            }
                        }
                        finally { File.Delete(candidate); }
                        return working.Size;
                    }
                    double scale = Math.Min(0.9, Math.Sqrt(limit / (double)size) * 0.9);
                    var scaled = new Bitmap(Math.Max(1, (int)(working.Width * scale)),
                        Math.Max(1, (int)(working.Height * scale)), PixelFormat.Format24bppRgb);
                    using (var graphics = Graphics.FromImage(scaled))
                    {
                        graphics.InterpolationMode = InterpolationMode.HighQualityBicubic;
                        graphics.DrawImage(working, new Rectangle(Point.Empty, scaled.Size));
                    }
                    if (working != image) working.Dispose();
                    working = scaled;
                }
            }
            finally { if (working != image) working.Dispose(); }
        }

        private static long Encode(Bitmap image, string path, long quality, CancellationToken token)
        {
            token.ThrowIfCancellationRequested();
            using (var parameters = new EncoderParameters(1))
            using (var stream = new FileStream(path, FileMode.Create, FileAccess.Write))
            {
                parameters.Param[0] = new EncoderParameter(Encoder.Quality, quality);
                image.Save(stream, JpegCodec, parameters);
                return stream.Length;
            }
        }
    }
}
