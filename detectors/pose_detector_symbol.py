import cv2
import numpy as np
import time

import sys
sys.path.insert(0, "/Users/guanghan.ning/Desktop/dev/CenterNet-Gluon/")

from external.nms import soft_nms_39
from models.tensor_utils import flip_tensor, flip_lr_off, flip_lr

from detectors.base_detector import BaseDetector

from models.decoder import decode_centernet_pose
from utils.post_process import multi_pose_post_process
from mxnet import nd


class PoseDetector(BaseDetector):
    def __init__(self, opt):
        super(PoseDetector, self).__init__(opt)
        self.flip_idx = opt.flip_idx

    def process(self, images, return_time=False):
        output = self.model(images)[-1]
        # 0: hm, 1: wh, 2: hps, 3: reg, 4: hm_hp, 5:hp_offset
        output[0] = output[0].sigmoid()

        if self.opt.hm_hp and not self.opt.mse_loss:
            output[4] = output[4].sigmoid()

        reg = output[3] if self.opt.reg_offset else None
        hm_hp = output[4] if self.opt.hm_hp else None
        hp_offset = output[5] if self.opt.reg_hp_offset else None

        nd.waitall()
        forward_time = time.time()

        if self.opt.flip_test:
            output[0] = (output[0][0:1] + flip_tensor(output[0][1:2])) / 2
            output[1] = (output[1][0:1] + flip_tensor(output[1][1:2])) / 2
            output[2] = (output[2][0:1] + flip_lr_off(output[2][1:2], self.flip_idx)) / 2
            hm_hp = (hm_hp[0:1] + flip_lr(hm_hp[1:2], self.flip_idx)) / 2 if hm_hp is not None else None
            reg = reg[0:1] if reg is not None else None
            hp_offset = hp_offset[0:1] if hp_offset is not None else None

        dets = decode_centernet_pose(output[0], output[1], output[2], reg=reg, hm_hp=hm_hp, hp_offset=hp_offset, K=self.opt.K)

        if return_time:
            return output, dets, forward_time
        else:
            return output, dets

    def post_process(self, dets, meta, scale=1):
        dets = dets.asnumpy()
        dets = multi_pose_post_process(dets.copy(), [meta['c']], [meta['s']], meta['out_height'], meta['out_width'])

        for j in range(1, self.num_classes + 1):
            dets[0][j] = np.array(dets[0][j], dtype=np.float32).reshape(-1, 39)
            dets[0][j][:, :4] /= scale
            dets[0][j][:, 5:] /= scale
        return dets[0]

    def merge_outputs(self, detections):
        results = {}
        results[1] = np.concatenate([detection[1] for detection in detections], axis=0).astype(np.float32)
        if self.opt.nms or len(self.opt.test_scales) > 1:
            soft_nms_39(results[1], Nt=0.5, method=2)
        results[1] = results[1].tolist()
        return results
