import java.io.BufferedInputStream;
import java.io.DataInputStream;
import java.io.FileInputStream;
import java.io.IOException;
import java.nio.ByteOrder;
import java.util.Arrays;

import net.csibio.aird.compressor.ComboComp;
import net.csibio.aird.compressor.bytecomp.ZstdWrapper;
import net.csibio.aird.compressor.intcomp.VarByteWrapper;

public class ComboCompFileBench {
    private static int readIntLE(DataInputStream in) throws IOException {
        int b0 = in.readUnsignedByte();
        int b1 = in.readUnsignedByte();
        int b2 = in.readUnsignedByte();
        int b3 = in.readUnsignedByte();
        return (b0) | (b1 << 8) | (b2 << 16) | (b3 << 24);
    }

    private static int[] readIntFile(String path) throws IOException {
        try (DataInputStream in = new DataInputStream(new BufferedInputStream(new FileInputStream(path), 1 << 20))) {
            byte[] magic = new byte[4];
            in.readFully(magic);
            if (magic[0] != 'T' || magic[1] != 'C' || magic[2] != 'C' || magic[3] != 'I') {
                throw new IOException("Bad magic for " + path);
            }
            int endian = in.readUnsignedByte();
            if (endian != 0) {
                throw new IOException("Unsupported endian flag " + endian + " for " + path);
            }
            int count = readIntLE(in);
            int[] values = new int[count];
            for (int i = 0; i < count; i++) {
                values[i] = readIntLE(in);
            }
            return values;
        }
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 1) {
            throw new IllegalArgumentException("Usage: ComboCompFileBench <int32_stream_file>");
        }
        int[] values = readIntFile(args[0]);
        VarByteWrapper intComp = new VarByteWrapper();
        ZstdWrapper byteComp = new ZstdWrapper();

        long t0 = System.nanoTime();
        byte[] encoded = ComboComp.encode(intComp, byteComp, values);
        long t1 = System.nanoTime();
        int[] decoded = ComboComp.decode(intComp, byteComp, encoded);
        long t2 = System.nanoTime();

        if (!Arrays.equals(values, decoded)) {
            throw new IllegalStateException("ComboComp roundtrip mismatch for " + args[0]);
        }

        long rawBytes = (long) values.length * 4L;
        System.out.println("{"
            + "\"input_path\":\"" + args[0].replace("\\", "\\\\").replace("\"", "\\\"") + "\","
            + "\"input_ints\":" + values.length + ","
            + "\"raw_bytes\":" + rawBytes + ","
            + "\"compressed_bytes\":" + encoded.length + ","
            + "\"compression_ratio\":" + (encoded.length == 0 ? 0.0 : ((double) rawBytes / (double) encoded.length)) + ","
            + "\"encode_time_s\":" + ((t1 - t0) / 1_000_000_000.0) + ","
            + "\"decode_time_s\":" + ((t2 - t1) / 1_000_000_000.0) + ","
            + "\"int_comp\":\"" + intComp.getName() + "\","
            + "\"byte_comp\":\"" + byteComp.getName() + "\","
            + "\"byte_order\":\"" + ByteOrder.LITTLE_ENDIAN + "\""
            + "}");
    }
}
