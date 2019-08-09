'''
Objects as Points
Mxnet adaptation of the Official Pytorch Implementation

Author: Guanghan Ning
Date: August, 2019
'''

from mxnet import gluon, init, nd
from mxnet.gluon import nn

"""
1. Basic Re-usable blocks
"""

class convolution(nn.Block):
    def __init__(self, kernel_size, channels_out, channels_in=0, strides=1, with_bn=True, **kwargs):
        super(convolution, self).__init__(**kwargs)
        paddings  = (kernel_size - 1)//2  # determine paddings to keep resolution unchanged
        with self.name_scope():
            self.conv = nn.Conv2D(channels_out, kernel_size, strides, paddings, in_channels=channels_in, use_bias= not with_bn) # infer input shape if not specified
            self.bn   = nn.BatchNorm(in_channels= channels_out) if with_bn else nn.Sequential()
    
    def forward(self, X):
        conv = self.conv(X)
        bn   = self.bn(conv)
        return nd.relu(bn)


def test_convolution_shape():
    blk = convolution(kernel_size=3, channels_out=128)
    blk.initialize()
    X = nd.random.uniform(shape=(1, 64, 128, 128))
    Y = blk(X)
    print("\t output shape:", Y.shape)


class fully_connected(nn.Block):
    def __init__(self, channels_out, channels_in = 0, with_bn=True, **kwargs):
        super(fully_connected, self).__init__(**kwargs)
        with self.name_scope():
            self.with_bn = with_bn
            self.linear  = nn.Dense(channels_out, in_units=channels_in) if channels_in else nn.Dense(channels_out)
            if self.with_bn:
                self.bn  = nn.BatchNorm(in_channels=channels_out)
    
    def forward(self, X):
        linear = self.linear(X)
        bn = self.bn(linear) if self.with_bn else linear
        return nd.relu(bn)


def test_fully_connected_shape():
    blk = fully_connected(channels_out=128)
    blk.initialize()
    X   = nd.random.uniform(shape=(1, 2, 32, 32))
    Y   = blk(X)
    print("\t output shape:", Y.shape)


class residual(nn.Block):
    def __init__(self, channels_out, channels_in, stride=1, with_bn=True, **kwargs):
        super(residual, self).__init__(**kwargs)
        with self.name_scope():
            self.conv1 = nn.Conv2D(channels_out, kernel_size=(3,3), strides=(stride, stride), padding=(1,1), in_channels=channels_in, use_bias=False)
            self.bn1   = nn.BatchNorm(in_channels= channels_out)
            
            self.conv2 = nn.Conv2D(channels_out, kernel_size=(3,3), strides=(1, 1), padding=(1,1), use_bias=False)
            self.bn2   = nn.BatchNorm(in_channels= channels_out)

            self.skip = nn.Sequential() 
            if stride != 1 or channels_in != channels_out:
                with self.name_scope():
                    self.skip.add( nn.Conv2D(channels_out, kernel_size=(1,1), use_bias=False),
                                nn.BatchNorm(in_channels= channels_out)
                )

    def forward(self, X):
        conv1 = self.conv1(X)
        bn1   = self.bn1(conv1)
        relu1 = nd.relu(bn1)

        conv2 = self.conv2(relu1)
        bn2   = self.bn2(conv2)

        skip  = self.skip(X)
        return nd.relu(bn2 + skip)


def test_residual():
    blk = residual(channels_out=32, channels_in=64)
    blk.initialize()
    X   = nd.random.uniform(shape=(1, 64, 128, 128))
    Y   = blk(X)
    print("\t output shape:", Y.shape)


class bilinear_upsample(nn.Block):
    def __init__(self, scale_factor=2, **kwargs):
        super(bilinear_upsample, self).__init__(**kwargs)
        self.scale_factor = 2
    
    def forward(self, X):
        return nd.UpSampling(X, scale=2, sample_type='bilinear')

"""
2. Utils to re-use basic blocks; Factories for repetitive computations
"""
def make_repeat_layers(kernel_size, channels_out, channels_in, num_modules, layer=convolution, **kwargs):
    layers = [layer(kernel_size, channels_out, channels_in, **kwargs)]
    for _ in range(1, num_modules):
        layers.append(layer(kernel_size, channels_out, channels_out, **kwargs))
    sequential = nn.Sequential()
    sequential.add(*layers)
    return sequential

def make_repeat_layers_reverse(kernel_size, channels_out, channels_in, num_modules, layer=convolution, **kwargs):
    layers = [layer(kernel_size, channels_in, channels_in, **kwargs) for _ in range(num_modules-1)] 
    layers.append(layer(kernel_size, channels_out, channels_in, **kwargs))
    sequential = nn.Sequential()
    sequential.add(*layers)
    return sequential

class MergeUp(nn.Block):
    def forward(self, up1, up2):
        return up1 + up2

def make_merge_layer():
    return MergeUp()

def make_pool_layer():
    #return nn.MaxPool2D(pool_size=2)
    return nn.Sequential()

def make_unpool_layer():
    return bilinear_upsample(scale_factor=2)

def make_keypoint_layer(channels_out, channels_intermediate, channels_in):
    sequential = nn.Sequential()
    sequential.add(convolution(kernel_size=3, channels_out=channels_intermediate, channels_in=channels_in, with_bn=False))
    sequential.add(nn.Conv2D(channels_out, kernel_size=1))
    return sequential

def make_inter_layer(channels):
    return residual(channels, channels)

def make_conv_layer(channels_out, channels_in):
    return convolution(3, channels_out, channels_in)

def make_hg_layer(kernel_size, channels_out, channels_in, mod, layer=convolution, **kwargs):
    layers = [layer(kernel_size, channels_out, channels_in, strides=2)]
    layers += [layer(kernel_size, channels_out, channels_out) for _ in range(mod-1)]
    sequential = nn.Sequential()
    sequential.add(*layers)
    return sequential


"""
3. Structures that are higher-level than basic blocks
"""
class  keypoint_struct(nn.Block):
    def __init__(self,
                 level, dims, num_blocks, 
                 layer= residual,
                 make_up_layer = make_repeat_layers, make_low_layer=make_repeat_layers,
                 make_hg_layer = make_repeat_layers, make_hg_layer_reverse=make_repeat_layers_reverse,
                 make_pool_layer = make_pool_layer, make_unpool_layer=make_unpool_layer,
                 make_merge_layer = make_merge_layer, **kwargs):
        super(keypoint_struct, self).__init__()

        self.level = level

        curr_num_blocks = num_blocks[0]
        next_num_blocks = num_blocks[1]

        curr_dim = dims[0]
        next_dim = dims[1]

        with self.name_scope():
            self.up1  = make_up_layer(3, curr_dim, curr_dim, curr_num_blocks, layer=layer, **kwargs)
            self.max1 = make_pool_layer()
            self.low1 = make_hg_layer(3, next_dim, curr_dim, curr_num_blocks, layer=layer, **kwargs)
            self.low2 = keypoint_struct(
                level-1, dims[1:], num_blocks[1:], layer=layer, **kwargs
            ) if self.level > 1 else \
                make_low_layer(
                    3, next_dim, next_dim, next_num_blocks, layer=layer, **kwargs
                )
            self.low3 = make_hg_layer_reverse(
                3, curr_dim, next_dim, curr_num_blocks, layer=layer, **kwargs
            )
            self.up2 = make_unpool_layer()
            self.merge = make_merge_layer()
    
    def forward(self, X):
        up1  = self.up1(X)
        max1 = self.max1(X)
        low1 = self.low1(max1)
        low2 = self.low2(low1)
        low3 = self.low3(low2)
        up2  = self.up2(low3)
        return self.merge(up1, up2)

        
"""
4. Stacked Houglass Network
"""
class stacked_hourglass(nn.Block):
    def __init__(self, level, num_stacks, 
                 dims, num_blocks, heads,
                 pre=None, conv_dim=256, 
                 make_conv_layer = make_conv_layer,     make_heat_layer    = make_keypoint_layer,
                 make_tag_layer  = make_keypoint_layer, make_regress_layer = make_keypoint_layer,
                 make_up_layer   = make_repeat_layers,  make_low_layer     = make_repeat_layers,
                 make_hg_layer   = make_repeat_layers,  make_hg_layer_reverse = make_repeat_layers_reverse,
                 make_pool_layer = make_pool_layer,     make_unpool_layer     = make_unpool_layer,
                 make_merge_layer= make_merge_layer,    make_inter_layer      = make_inter_layer,
                 kp_layer = residual
    ):
        super(stacked_hourglass, self).__init__()

        self.num_stacks = num_stacks
        self.heads      = heads
        
        curr_dim = dims[0]
        
        with self.name_scope():
            self.pre = nn.Sequential(
                convolution(7, 128, 3, strides=2),
                residual(3, 256, 128, strides=2)
            ) if pre is None else pre

        self.kpts = nn.HybridSequential()
        with self.name_scope():
            for _ in range(num_stacks):
                self.kpts.add(
                    keypoint_struct(level, dims, num_blocks,
                                    make_up_layer = make_up_layer,
                                    make_low_layer = make_low_layer,
                                    make_hg_layer = make_hg_layer,
                                    make_hg_layer_reverse = make_hg_layer_reverse,
                                    make_pool_layer = make_pool_layer,
                                    make_unpool_layer = make_unpool_layer,
                                    make_merge_layer = make_merge_layer
                    )
                )
        
        self.convs = nn.HybridSequential()
        with self.name_scope():
            for _ in range(num_stacks):
                self.convs.add(
                    make_conv_layer(conv_dim, curr_dim)
                )
        

        self.inters = nn.HybridSequential()
        with self.name_scope():
            for _ in range(num_stacks-1):
                self.inters.add(
                    nn.Sequential(
                        nn.Conv2D(curr_dim, (1,1), use_bias=False, in_channels=conv_dim),
                        nn.BatchNorm()
                    )
                )
        
        self.convs_ = nn.HybridSequential()
        with self.name_scope():
            for _ in range(num_stacks-1):
                self.convs_.add(
                    nn.Sequential(
                        nn.Conv2D(curr_dim, (1,1), use_bias=False, in_channels=conv_dim),
                        nn.BatchNorm()
                    )
                )
        
        # keypoint heatmaps
        for head in heads.keys():
            if "hm" in head:
                










def peek_network(net, input_shape=(224, 224)):
    w, h = input_shape
    X = nd.random.uniform(shape=(1,1,w,h))
    net.initialize()
    for layer in net:
        X = layer(X)
        print(layer.name, 'output shape: {}'.format(X.shape))
    return

def test_all():
    funcs = [test_convolution_shape, test_fully_connected_shape, test_residual]
    for func in funcs:
        print("Testing routine: {}".format(func.__name__))
        func()


if __name__ == "__main__":
    #test_all()

    sequential = make_repeat_layers(1,3,3,1,convolution)
    print(sequential)
