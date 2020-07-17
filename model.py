import tensorflow.keras.models
import tensorflow.keras.layers
import tensorflow.keras.optimizers
from matplotlib import gridspec
from tensorflow.keras.models import Model
from keras_utils import *
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.layers import Flatten, MaxPooling2D
import tensorflow.compat.v1 as tf
import matplotlib.pyplot as plt
import tensorflow.keras.backend as K
from utils import *
from tensorflow import keras


class MriGAN:
    def __init__(self, sess, flags):

        self.mi = 500
        self.flags = flags
        self.sess = sess
        self.img_shape = (256, 256, 1)
        self.img_size = (256, 256, 1)
        self.discriminator_optimizer = Adam(lr=0.00005, beta_1=0.5)
        self.generator_optimizer = Adam(lr=0.0002, beta_1=0.5)
        self.loss_obj = tensorflow.keras.losses.BinaryCrossentropy(from_logits=True)
        self.save_iter = 500
        self.batch_size = flags.batch_size
        self.mutual_information = self.custom_mi_losses([[-1.0, 1.0], [-1.0, 1.0]], 256, False)

        self._build_net()

        self.sample_image_output_path = "../tc2mResults"

    def _set_discriminator(self):
        self.discriminator = Discriminator(self.img_shape)()
        self.discriminator.compile(loss='binary_crossentropy',
                                   optimizer=self.discriminator_optimizer,
                                   metrics=['accuracy'])

    def _set_generator(self):
        self.generator = Generator(self.img_shape)()
        self.generator.compile(loss=self.ssim_loss,
                               optimizer=self.generator_optimizer,
                               metrics=['accuracy'])

    def _set_combined_model(self):
        self.combined_model = Model(self.input, self.valid)
        self.custom_binary_losses = self.binary_cross_with_mutual_mi()
        self.combined_model.compile(optimizer='adam',
                                    loss=self.custom_binary_losses
                                    )

    @staticmethod
    def get_jh(x, y, value_range, nbins):
        dtype = tf.dtypes.int32
        x_range = value_range[0]
        y_range = value_range[1]
        histy_bins = tf.histogram_fixed_width_bins(y, y_range, nbins=nbins, dtype=dtype)

        def masking_info(tf_val):
            return tf.math.equal(histy_bins, tf_val)

        H = tf.map_fn(lambda i: tf.histogram_fixed_width(x[masking_info(i)],
                                                         x_range,
                                                         nbins=nbins
                                                         ),
                      tf.range(nbins))

        return H

    @staticmethod
    def get_s1(eager_tensor):
        sum_tensor = tf.reduce_sum(eager_tensor, axis=0)
        reshape_tensor = tf.reshape(sum_tensor, [-1, eager_tensor.shape[0]])
        return reshape_tensor

    @staticmethod
    def get_s2(eager_tensor):
        sum_tensor = tf.reduce_sum(eager_tensor, axis=1)
        reshape_tensor = tf.reshape(sum_tensor, [eager_tensor.shape[1], -1])

        return reshape_tensor

    def custom_mi_losses(self, value_range, n_bins, normalized):
        joint_entropy = self.get_jh
        s1_func = self.get_s1
        s2_func = self.get_s2

        def _generator_mi_losses(real_image, generated_image):

            y_pred_flatten = K.flatten(generated_image)
            y_true_flatten = K.flatten(real_image)

            jh = joint_entropy(y_true_flatten, y_pred_flatten, value_range, n_bins)
            jh = tf.dtypes.cast(jh, tf.float32)

            real_image_prob = s1_func(jh)
            generated_image_prob = s2_func(jh)

            if normalized:
                mi = ((K.sum(real_image_prob * K.log(real_image_prob))
                       + K.sum(generated_image_prob * K.log(generated_image_prob)))
                      / K.sum(jh * K.log(jh))) - 1
            else:
                mi = (K.sum(jh * K.log(jh)) - K.sum(real_image_prob * K.log(real_image_prob))
                      - K.sum(generated_image_prob * K.log(generated_image_prob)))

            return -1 * K.mean(mi)

        return _generator_mi_losses

    def binary_cross_with_mutual_mi(self):
        mi_loss = self.mi

        def loss(y_true, y_pred):
            loss_obj = tf.keras.losses.BinaryCrossentropy()
            binary_loss = loss_obj(y_true, y_pred) + mi_loss
            total_loss = binary_loss + mi_loss * 0.5

            return total_loss

        return loss

    def _combined_generator_discriminator(self):
        # gen_img and valid type must be tensor
        self.input = keras.Input(shape=self.img_shape)
        self.gen_img = self.generator(self.input)

        self.discriminator.trainable = False

        self.valid = self.discriminator(self.gen_img)

    def _build_net(self):
        self._set_discriminator()
        self._set_generator()
        self._combined_generator_discriminator()
        self._set_combined_model()

    def mutual_information_loss_func(self, value_range, n_bins):
        def get_loss(y_pred, y_true):

            y_pred_flatten = K.flatten(y_pred)
            y_true_flatten = K.flatten(y_true)

            sigma = 1
            normalized = False

            EPS = K.epsilon()

            jh = self.get2d_histogram(y_pred_flatten, y_true_flatten, value_range, nbins=n_bins,
                                      dtype=tf.dtypes.int32)

            sh = K.sum(jh)
            jh = jh / sh
            s1 = K.reshape(K.sum(jh, axis=0), (-1, jh.shape[0]))
            # s1 = K.sum(jh, axis=0).reshape((-1, jh.shape[0]))
            s2 = K.reshape(K.sum(jh, axis=1), (jh.shape[1], -1))
            # s2 = K.sum(jh, axis=1).reshape((jh.shape[1], -1))

            if normalized:
                mi = ((K.sum(s1 * K.log(s1)) + K.sum(s2 * K.log(s2)))
                      / K.sum(jh * K.log(jh))) - 1

            else:
                mi = (K.sum(jh * K.log(jh)) - K.sum(s1 * K.log(s1))
                      - K.sum(s2 * K.log(s2)))

            return -K.mean(mi)

        return get_loss

    @staticmethod
    def ssim_loss(y_true, y_pred):
        # max_val is 1.0 because y_true and y_pred is zero centered
        return tf.reduce_mean(tf.image.ssim(y_true, y_pred, max_val=2.0))

    @staticmethod
    def get2d_histogram(x, y,
                        value_range,
                        nbins=100,
                        dtype=tf.dtypes.int32):
        """
        Bins x, y coordinates of points onto simple square 2d histogram
        Given the tensor x and y:
        x: x coordinates of points
        y: y coordinates of points
        this operation returns a rank 2 `Tensor`
        representing the indices of a histogram into which each element
        of `values` would be binned. The bins are equal width and
        determined by the arguments `value_range` and `nbins`.
      Args:
        x:  Numeric `Tensor`.
        y: Numeric `Tensor`.
        value_range[0] lims for x
        value_range[1] lims for y
        nbins:  Scalar `int32 Tensor`.  Number of histogram bins.
        dtype:  dtype for returned histogram.
        """
        x_range = value_range[0]
        y_range = value_range[1]

        histy_bins = tf.histogram_fixed_width_bins(y, y_range, nbins=nbins, dtype=dtype)

        H = tf.map_fn(lambda i: tf.histogram_fixed_width(x[histy_bins == i], x_range, nbins=nbins),
                      tf.range(nbins))
        return H  # Matrix!

    @staticmethod
    def mutual_information_2d(x, y):

        # to analyze image mutual information
        # flatten numpy 2d array to 1d array
        x = x.ravel()
        y = y.ravel()

        sigma = 1
        normalized = False
        EPS = np.finfo(float).eps

        bins = 256
        jh = np.histogram2d(x, y, bins=bins)[0]
        # smooth the jh with a gaussian filter of given sigma
        # compute marginal histograms
        jh = jh + EPS
        sh = K.sum(jh)
        jh = jh / sh
        s1 = K.sum(jh, axis=0).reshape((-1, jh.shape[0]))
        s2 = K.sum(jh, axis=1).reshape((jh.shape[1], -1))

        # Normalised Mutual Information of:
        # Studholme,  jhill & jhawkes (1998).
        # "A normalized entropy measure of 3-D medical image alignment".
        # in Proc. Medical Imaging 1998, vol. 3338, San Diego, CA, pp. 132-143.
        if normalized:
            mi = ((K.sum(s1 * K.log(s1)) + K.sum(s2 * K.log(s2)))
                  / K.sum(jh * K.log(jh))) - 1
        else:
            mi = (K.sum(jh * K.log(jh)) - K.sum(s1 * K.log(s1))
                  - K.sum(s2 * K.log(s2)))

        return -mi

    def train_discriminator(self, real_ct, gen_ct):
        dis_valid_np_arr = np.ones((self.batch_size, 1))
        dis_fake_np_arr = np.zeros((self.batch_size, 1))

        d_loss_real = self.discriminator.train_on_batch(real_ct, dis_valid_np_arr)
        d_loss_fake = self.discriminator.train_on_batch(gen_ct, dis_fake_np_arr)

        d_loss_total = np.add(d_loss_real, d_loss_fake) * 0.5

        print("d_loss_total = ", d_loss_total[0], "accuracy = ", d_loss_total[1])

    def train_generator(self, input_mr, input_ct):
        g_ssim_loss = self.generator.train_on_batch(input_mr, input_ct)

        print("g_ssim_loss = ", g_ssim_loss)

    def train_combined_model(self, input_mr):
        dis_valid_np_arr = np.ones((self.batch_size, 1))
        combined_loss = self.combined_model.train_on_batch(input_mr, dis_valid_np_arr)
        print("combined_loss = ", combined_loss)

    def train_steps(self, epoch_num, steps_per_epochs, batch_img_generator):

        img_ct, img_mr, img_ct_ori, img_mr_ori, img_names = batch_img_generator.get_next()

        for steps in range(steps_per_epochs):
            img_ct_np_arr, img_mr_np_arr = self.sess.run([img_ct, img_mr])
            # gen_ct is numpy array shape (batch_size, 256, 256, 1)
            gen_ct = self.generator.predict(img_mr_np_arr)
            self.mi = self.mutual_information(img_ct, gen_ct)
            self.discriminator.trainable = True
            self.train_discriminator(img_ct_np_arr, gen_ct)

            self.discriminator.trainable = False
            self.train_generator(img_mr_np_arr, img_ct)
            self.train_combined_model(img_mr_np_arr)

            if steps == steps_per_epochs - 1:
                return self.sampling(epoch_num, img_mr, img_ct, gen_ct)

    def sampling_images(self, mri_batch_tensor, ct_batch_tensor, gen_ct_batch_numpy):
        mri_batch_image, ct_batch_image = self.sess.run(
            [mri_batch_tensor, ct_batch_tensor])

        gen_ct_batch_image = gen_ct_batch_numpy

        # return batch image type is numpy array
        return [mri_batch_image, ct_batch_image, gen_ct_batch_image]

    def sampling(self, epoch, mri_batch_tensor, ct_batch_tensor, gen_ct_batch_tensor):
        images = self.sampling_images(mri_batch_tensor, ct_batch_tensor, gen_ct_batch_tensor)
        return images

    @staticmethod
    def plots(imgs, iter_time, image_size, save_file):
        scale, margin = 0.02, 0.02
        n_cols, n_rows = len(imgs), imgs[0].shape[0]
        cell_size_h, cell_size_w = imgs[0].shape[1] * scale, imgs[0].shape[2] * scale

        fig = plt.figure(figsize=(cell_size_w * n_cols, cell_size_h * n_rows))  # (column, row)
        gs = gridspec.GridSpec(n_rows, n_cols)  # (row, column)
        gs.update(wspace=margin, hspace=margin)

        imgs = [utils.inverse_transform(imgs[idx]) for idx in range(len(imgs))]

        # save more bigger image
        for col_index in range(n_cols):
            for row_index in range(n_rows):
                ax = plt.subplot(gs[row_index * n_cols + col_index])
                plt.axis('off')
                ax.set_xticklabels([])
                ax.set_yticklabels([])
                ax.set_aspect('equal')
                plt.imshow((imgs[col_index][row_index]).reshape(image_size[0], image_size[1]), cmap='Greys_r')

        plt.savefig(save_file + '/sample_{}.png'.format(str(iter_time).zfill(5)), bbox_inches='tight')
        plt.close(fig)

    @staticmethod
    def _save_model(mr_img, ct_img, gen_ct_img):

        pass


class Discriminator:

    def __init__(self, input_size):
        self.inputs = keras.layers.Input(input_size)
        self.outputs = self._networks(self.inputs)

        self.optimizer = Adam(lr=0.00005, beta_1=0.5)

    @classmethod
    def _networks(cls, inputs):
        conv1 = discriminator_conv(2, inputs)
        pool1 = MaxPooling2D((2, 2))(conv1)
        conv2 = discriminator_conv(4, pool1)
        conv3 = discriminator_conv(8, conv2)
        conv4 = discriminator_conv(16, conv3)
        conv5 = discriminator_conv(32, conv4)
        conv6 = discriminator_conv(64, conv5)

        flat = Flatten()(conv6)

        dense1 = discriminator_dense(8 * 8 * 64, flat)
        dense2 = discriminator_dense(4068, dense1)
        dense3 = discriminator_dense(2048, dense2)
        dense4 = discriminator_dense(1024, dense3)
        dense5 = discriminator_dense(512, dense4)

        final_layer = discriminator_final_layer(dense5)

        return final_layer

    def _load_data(self):
        return

    def __call__(self, *args, **kwargs):
        return tensorflow.keras.models.Model(self.inputs, self.outputs)


class Generator:

    def __init__(self, input_size, *args, **kwargs):
        self.inputs = keras.layers.Input(input_size)
        self.outputs = self._networks(self.inputs)

        self.loss_obj = keras.losses.BinaryCrossentropy(from_logits=True)
        self.optimizer = Adam(lr=0.0002, beta_1=0.5)
        # super().__init__(
        #     inputs=inputs,
        #     outputs=outputs
        # )
        # set adam optimizer

    def generator_loss(self, generated_image):
        generated_loss = self.loss_obj(tf.ones_like(generated_image), generated_image)
        # TODO : Use L1 or L2 loss
        return generated_loss

    @classmethod
    def _networks(cls, inputs):
        batch1, pool1 = encoder_conv(32, inputs)
        batch2, pool2 = encoder_conv(64, pool1)
        batch3, pool3 = encoder_conv(128, pool2)
        batch4, pool4 = encoder_conv(256, pool3)
        batch5, pool5 = encoder_conv(512, pool4)

        up1 = encoder_to_decoder_conv(1024, pool5)

        up2 = decoder_conv(512, up1, batch5)
        up3 = decoder_conv(256, up2, batch4)
        up4 = decoder_conv(128, up3, batch3)
        up5 = decoder_conv(64, up4, batch2)

        outputs = generator_final_layer(32, up5, batch1)
        return outputs

    def __call__(self, *args, **kwargs):
        return tensorflow.keras.models.Model(self.inputs, self.outputs)
