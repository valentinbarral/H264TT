#include <inttypes.h>
#include <stdio.h>
#include <stdlib.h>

#include <libavcodec/avcodec.h>
#include <libavformat/avformat.h>
#include <libavutil/motion_vector.h>

static AVFormatContext *fmt_ctx = NULL;
static AVCodecContext *video_dec_ctx = NULL;
static AVStream *video_stream = NULL;
static const char *src_filename = NULL;
static int video_stream_idx = -1;
static AVFrame *frame = NULL;
static int video_frame_count = 0;

static int decode_packet(const AVPacket *pkt) {
    int ret = avcodec_send_packet(video_dec_ctx, pkt);
    if (ret < 0) {
        fprintf(stderr, "Error while sending a packet to the decoder: %d\n", ret);
        return ret;
    }

    while (ret >= 0) {
        ret = avcodec_receive_frame(video_dec_ctx, frame);
        if (ret == AVERROR(EAGAIN) || ret == AVERROR_EOF) {
            break;
        } else if (ret < 0) {
            fprintf(stderr, "Error while receiving a frame from the decoder: %d\n", ret);
            return ret;
        }

        AVFrameSideData *sd = av_frame_get_side_data(frame, AV_FRAME_DATA_MOTION_VECTORS);
        if (sd) {
            const AVMotionVector *mvs = (const AVMotionVector *)sd->data;
            int mv_count = sd->size / (int)sizeof(*mvs);
            for (int i = 0; i < mv_count; i++) {
                const AVMotionVector *mv = &mvs[i];
                printf(
                    "%d,%d,%d,%d,%d,%d,%d,%d,%d,%d,%d\n",
                    video_frame_count,
                    mv->source,
                    mv->w,
                    mv->h,
                    mv->src_x,
                    mv->src_y,
                    mv->dst_x,
                    mv->dst_y,
                    mv->motion_x,
                    mv->motion_y,
                    mv->motion_scale
                );
            }
        }

        video_frame_count++;
        av_frame_unref(frame);
    }

    return 0;
}

static int open_codec_context(AVFormatContext *format_ctx) {
    int ret;
    const AVCodec *dec = NULL;
    AVDictionary *opts = NULL;

    ret = av_find_best_stream(format_ctx, AVMEDIA_TYPE_VIDEO, -1, -1, &dec, 0);
    if (ret < 0) {
        fprintf(stderr, "Could not find video stream in input file '%s'\n", src_filename);
        return ret;
    }

    int stream_idx = ret;
    AVStream *st = format_ctx->streams[stream_idx];

    video_dec_ctx = avcodec_alloc_context3(dec);
    if (!video_dec_ctx) {
        fprintf(stderr, "Failed to allocate codec context\n");
        return AVERROR(ENOMEM);
    }

    ret = avcodec_parameters_to_context(video_dec_ctx, st->codecpar);
    if (ret < 0) {
        fprintf(stderr, "Failed to copy codec parameters to codec context\n");
        return ret;
    }

    av_dict_set(&opts, "flags2", "+export_mvs", 0);
    ret = avcodec_open2(video_dec_ctx, dec, &opts);
    av_dict_free(&opts);
    if (ret < 0) {
        fprintf(stderr, "Failed to open video codec\n");
        return ret;
    }

    video_stream_idx = stream_idx;
    video_stream = st;
    return 0;
}

int main(int argc, char **argv) {
    int ret = 0;
    AVPacket *pkt = NULL;

    if (argc != 2) {
        fprintf(stderr, "Usage: %s <video>\n", argv[0]);
        return 1;
    }

    src_filename = argv[1];

    if (avformat_open_input(&fmt_ctx, src_filename, NULL, NULL) < 0) {
        fprintf(stderr, "Could not open source file %s\n", src_filename);
        return 1;
    }

    if (avformat_find_stream_info(fmt_ctx, NULL) < 0) {
        fprintf(stderr, "Could not find stream information\n");
        ret = 1;
        goto end;
    }

    if (open_codec_context(fmt_ctx) < 0 || !video_stream) {
        ret = 1;
        goto end;
    }

    frame = av_frame_alloc();
    pkt = av_packet_alloc();
    if (!frame || !pkt) {
        fprintf(stderr, "Could not allocate frame or packet\n");
        ret = 1;
        goto end;
    }

    printf("frame_index,source,w,h,src_x,src_y,dst_x,dst_y,motion_x,motion_y,motion_scale\n");

    while (av_read_frame(fmt_ctx, pkt) >= 0) {
        if (pkt->stream_index == video_stream_idx) {
            ret = decode_packet(pkt);
            if (ret < 0) {
                av_packet_unref(pkt);
                break;
            }
        }
        av_packet_unref(pkt);
    }

    if (ret >= 0) {
        decode_packet(NULL);
    }

end:
    avcodec_free_context(&video_dec_ctx);
    avformat_close_input(&fmt_ctx);
    av_frame_free(&frame);
    av_packet_free(&pkt);
    return ret < 0;
}
