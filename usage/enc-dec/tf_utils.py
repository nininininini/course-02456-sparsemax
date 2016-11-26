import tensorflow as tf
from tensorflow.python.ops import tensor_array_ops
from tensorflow.python.framework import ops
from tensorflow.python.ops import nn_ops
from tensorflow.python.ops import math_ops
#from tensorflow_sparsemax.kernel import sparsemax, sparsemax_loss

# NOTE:
# importing custom tf kernel containing sparsemax op.
# assumes existance of a ../../tensorflow_sparsemax/kernel
# containing a compiled sparsemax kernel
import os.path as path
import sys

thisdir = path.dirname(path.realpath("__file__"))
sys.path.append(path.join(thisdir, '../../tensorflow_sparsemax'))

from kernel import sparsemax, sparsemax_loss

###
# custom loss function, similar to tensorflows but uses 3D tensors
# instead of a list of 2D tensors
def sequence_loss_tensor(logits, targets, weights, num_classes, loss_type='softmax',
                         average_across_timesteps=True,
                         softmax_loss_function=None, name=None):
    """Weighted cross-entropy loss for a sequence of logits (per example).
    """
    with ops.op_scope([logits, targets, weights], name, "sequence_loss_by_example"):
        logits_flat = tf.reshape(logits, [-1, num_classes])
        
        targets = tf.reshape(targets, [-1])
        #print(tf.shape(logits_flat))
        if loss_type == 'softmax':
            #print(tf.shape(targets))
            crossent = nn_ops.sparse_softmax_cross_entropy_with_logits(
                    logits_flat, targets)
            print(tf.shape(crossent))
        elif loss_type == "sparsemax":
            probs = sparsemax(logits_flat)
            # need to convert targets to 1 hot encoding
            
            sparse_labels = tf.reshape(targets, [-1, 1])
            derived_size = tf.shape(targets)[0]
            indices = tf.reshape(tf.range(0, derived_size, 1), [-1, 1])
            concated = tf.concat(1, [indices, tf.cast(sparse_labels, tf.int32)])
            outshape = tf.pack([derived_size, num_classes])
            targets = tf.cast(tf.sparse_to_dense(concated, outshape, 1.0, 0.0), tf.float32)
            
            
            #print(tf.shape(targets))
            #print(tf.shape(probs))
            crossent = sparsemax_loss(logits_flat,
                    probs, targets)
            #print(tf.shape(crossent))
        else:
            raise Exception("unexpected loss_type input")
        crossent = crossent * tf.reshape(weights, [-1])
        crossent = tf.reduce_sum(crossent)
        total_size = math_ops.reduce_sum(weights)
        total_size += 1e-12 # to avoid division by zero
        crossent /= total_size
        return crossent


###
# a custom masking function, takes sequence lengths and makes masks
def mask(sequence_lengths):
    # based on this SO answer: http://stackoverflow.com/a/34138336/118173
    batch_size = tf.shape(sequence_lengths)[0]
    max_len = tf.reduce_max(sequence_lengths)

    lengths_transposed = tf.expand_dims(sequence_lengths, 1)

    rng = tf.range(max_len)
    rng_row = tf.expand_dims(rng, 0)

    return tf.less(rng_row, lengths_transposed)


###
# decoder with attention

def attention_decoder(attention_input, attention_lengths, initial_state,
                      target_input, target_input_lengths, num_units,
                      num_attn_units, embeddings, W_out, b_out, attention_fn,
                      name='decoder', swap=False):
    """Decoder with attention.
    Note that the number of units in the attention decoder must always
    be equal to the size of the initial state/attention input.
    """
    with tf.variable_scope(name):
        target_dims = target_input.get_shape()[2]
        attention_dims = attention_input.get_shape()[2]
        attn_len = tf.shape(attention_input)[1]
        max_sequence_length = tf.reduce_max(target_input_lengths)

        weight_initializer = tf.truncated_normal_initializer(stddev=0.1)
        # map initial state to num_units
        W_s = tf.get_variable('W_s',
                              shape=[attention_dims, num_units],
                              initializer=weight_initializer)
        b_s = tf.get_variable('b_s',
                              shape=[num_units],
                              initializer=tf.constant_initializer())

        # GRU
        W_z = tf.get_variable('W_z',
                              shape=[target_dims+num_units+attention_dims, num_units],
                              initializer=weight_initializer)
        W_r = tf.get_variable('W_r',
                              shape=[target_dims+num_units+attention_dims, num_units],
                              initializer=weight_initializer)
        W_c = tf.get_variable('W_c',
                              shape=[target_dims+num_units+attention_dims, num_units],
                              initializer=weight_initializer)
        b_z = tf.get_variable('b_z',
                              shape=[num_units],
                              initializer=tf.constant_initializer(1.0))
        b_r = tf.get_variable('b_r',
                              shape=[num_units],
                              initializer=tf.constant_initializer(1.0))
        b_c = tf.get_variable('b_c',
                              shape=[num_units],
                              initializer=tf.constant_initializer())

        # for attention
        W_a = tf.get_variable('W_a',
                              shape=[attention_dims, num_attn_units],
                              initializer=weight_initializer)
        U_a = tf.get_variable('U_a',
                              shape=[1, 1, attention_dims, num_attn_units],
                              initializer=weight_initializer)
        b_a = tf.get_variable('b_a',
                              shape=[num_attn_units],
                              initializer=tf.constant_initializer())
        v_a = tf.get_variable('v_a',
                              shape=[num_attn_units],
                              initializer=weight_initializer)

        # project initial state
        initial_state = tf.nn.tanh(tf.matmul(initial_state, W_s) + b_s)

        # TODO: don't use convolutions!
        # TODO: fix the bias (b_a)
        hidden = tf.reshape(attention_input, tf.pack([-1, attn_len, 1, attention_dims]))
        part1 = tf.nn.conv2d(hidden, U_a, [1, 1, 1, 1], "SAME")
        part1 = tf.squeeze(part1, [2])  # squeeze out the third dimension

        inputs = tf.transpose(target_input, perm=[1, 0, 2])
        input_ta = tensor_array_ops.TensorArray(tf.float32, size=1, dynamic_size=True)
        input_ta = input_ta.unpack(inputs)

        def decoder_cond(time, state, output_ta_t, attention_tracker):
            return tf.less(time, max_sequence_length)

        def decoder_body_builder(feedback=False):
            def decoder_body(time, old_state, output_ta_t, attention_tracker):
                if feedback:
                    def from_previous():
                        prev_1 = tf.matmul(old_state, W_out) + b_out
                        return tf.gather(embeddings, tf.argmax(prev_1, 1))
                    x_t = tf.cond(tf.greater(time, 0), from_previous, lambda: input_ta.read(0))
                else:
                    x_t = input_ta.read(time)

                # attention
                part2 = tf.matmul(old_state, W_a) + b_a
                part2 = tf.expand_dims(part2, 1)
                john = part1 + part2
                e = tf.reduce_sum(v_a * tf.tanh(john), [2])
                alpha = attention_fn(e)
                alpha = tf.to_float(mask(attention_lengths)) * alpha
                alpha = alpha / tf.reduce_sum(alpha, [1], keep_dims=True)
                attention_tracker = attention_tracker.write(time, alpha)
                context = tf.reduce_sum(tf.expand_dims(alpha, 2) * tf.squeeze(hidden), [1])

                # GRU
                con = tf.concat(1, [x_t, old_state, context])
                z = tf.sigmoid(tf.matmul(con, W_z) + b_z)
                r = tf.sigmoid(tf.matmul(con, W_r) + b_r)
                con = tf.concat(1, [x_t, r*old_state, context])
                c = tf.tanh(tf.matmul(con, W_c) + b_c)
                new_state = (1-z)*c + z*old_state

                output_ta_t = output_ta_t.write(time, new_state)

                return (time + 1, new_state, output_ta_t, attention_tracker)
            return decoder_body


        output_ta = tensor_array_ops.TensorArray(tf.float32, size=1, dynamic_size=True, infer_shape=False)
        attention_tracker = tensor_array_ops.TensorArray(tf.float32, size=1, dynamic_size=True, infer_shape=False)
        time = tf.constant(0)
        loop_vars = [time, initial_state, output_ta, attention_tracker]

        _, state, output_ta, _ = tf.while_loop(decoder_cond,
                                               decoder_body_builder(),
                                               loop_vars,
                                               swap_memory=swap)
        _, valid_state, valid_output_ta, valid_attention_tracker = tf.while_loop(decoder_cond,
                                                        decoder_body_builder(feedback=True),
                                                        loop_vars,
                                                        swap_memory=swap)

        dec_out = tf.transpose(output_ta.pack(), perm=[1, 0, 2])
        valid_dec_out = tf.transpose(valid_output_ta.pack(), perm=[1, 0, 2])
        valid_attention_tracker = tf.transpose(valid_attention_tracker.pack(), perm=[1, 0, 2])

        return dec_out, valid_dec_out, valid_attention_tracker
