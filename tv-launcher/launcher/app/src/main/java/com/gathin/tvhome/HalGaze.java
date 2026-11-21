package com.gathin.tvhome;

import java.util.Random;

/**
 * Where HAL is looking, -1..1 on each axis, as on his TV screen and the phone: a glance somewhere every 1 to 4.5
 * seconds (now and then a longer stare, often back to the middle), and every 35 to 120 seconds a slow look round
 * the room, left to right or right to left.
 */
final class HalGaze {
    private static final Random rnd = new Random();
    private static float fromX, fromY, toX, toY, moveAt = -1, nextAt;
    private static float roomAt = 40f, roomDir = 1f;

    private HalGaze() {
    }

    private static float ease(float x) {
        x = Math.max(0f, Math.min(1f, x));
        return x * x * (3f - 2f * x);
    }

    static synchronized float[] at(float t) {
        // the look round the room: 4 seconds, side to side and back to the middle
        float rt = t - roomAt;
        if (rt >= 0f && rt < 4f) {
            float x;
            if (rt < 1f) x = -roomDir * ease(rt / 0.35f);
            else if (rt < 2.4f) x = -roomDir + 2f * roomDir * ease((rt - 1f) / 0.6f);
            else x = roomDir - roomDir * ease((rt - 2.4f) / 0.5f);
            fromX = toX = 0f;
            fromY = toY = 0f;
            return new float[]{x, 0.05f};
        }
        if (rt >= 4f) {
            roomAt = t + 35f + rnd.nextFloat() * 85f;
            roomDir = rnd.nextBoolean() ? 1f : -1f;
            nextAt = t + 1f;
        }
        if (t >= nextAt) {
            float[] cur = current(t);
            fromX = cur[0];
            fromY = cur[1];
            if (rnd.nextFloat() < 0.35f) {
                toX = 0f;
                toY = 0f;
            } else {
                toX = (rnd.nextFloat() * 2f - 1f) * 0.9f;
                toY = (rnd.nextFloat() * 2f - 1f) * 0.6f;
            }
            moveAt = t;
            nextAt = t + (rnd.nextFloat() < 0.2f ? 5f + rnd.nextFloat() * 4f : 1f + rnd.nextFloat() * 3.5f);
        }
        return current(t);
    }

    private static float[] current(float t) {
        float e = moveAt < 0 ? 1f : ease((t - moveAt) / 0.25f);
        return new float[]{fromX + (toX - fromX) * e, fromY + (toY - fromY) * e};
    }
}
