import gym
import tensorflow as tf
import tensorflow.contrib.slim as slim
import random
import numpy as np

class QNetwork():
    def __init__(self,h_size,action_size, scope, reuse=None, img_size=160, learning_rate=0.0001):
        self.frame_in = tf.placeholder(tf.float32, [None, img_size * img_size * 3], name="frame_in")
        img_in = tf.reshape(self.frame_in, [-1,img_size, img_size, 3])

        conv1 = slim.convolution2d(scope=scope+"_conv1",inputs=img_in, num_outputs=32, kernel_size=[16,16], stride=[8, 8], padding="SAME", biases_initializer=None)
        conv2 = slim.convolution2d(scope=scope+"_conv2",inputs=conv1, num_outputs=64, kernel_size=[8, 8], stride=[4, 4], padding="SAME", biases_initializer=None)
        conv3 = slim.convolution2d(scope=scope+"_conv3",inputs=conv2, num_outputs=64, kernel_size=[3, 3], stride=[1, 1], padding="SAME", biases_initializer=None)
        conv4 = slim.convolution2d(scope=scope+"_conv4",inputs=conv3, num_outputs=h_size, kernel_size=[5, 5], stride=[1, 1], padding="VALID", biases_initializer=None)

        self.train_len = tf.placeholder(tf.int32,[])
        self.batch_size = tf.placeholder(tf.int32, [])
        conv_flat = tf.reshape(slim.flatten(conv4), [self.batch_size, self.train_len, h_size])

        cell = tf.nn.rnn_cell.BasicLSTMCell(h_size, state_is_tuple=True, reuse=False)
        self.state_init = cell.zero_state(self.batch_size, tf.float32)
        rnn, self.rnn_state = tf.nn.dynamic_rnn(cell, conv_flat,dtype=tf.float32, initial_state=self.state_init, scope=scope+"_rnn")
        #print(rnn)
        #print(self.rnn_state)
        rnn = tf.reshape(rnn, [-1, h_size])

        with tf.variable_scope("va_split", reuse=reuse):
            stream_a, stream_v = tf.split(rnn,2,1)
            w_a = tf.Variable(tf.random_normal([h_size//2, action_size]))
            w_v = tf.Variable(tf.random_normal([h_size//2, 1]))

            advantage = tf.matmul(stream_a, w_a)
            value = tf.matmul(stream_v, w_v)

        # salience = tf.gradients(advantage, img_in)
        with tf.variable_scope("predict", reuse=reuse):
            self.q_out = value + tf.subtract(advantage, tf.reduce_mean(advantage, 1, keep_dims=True))
        #print(self.q_out)
            self.pred = tf.argmax(self.q_out, axis=1)

            self.target_q = tf.placeholder(tf.float32, [None])
            self.actions = tf.placeholder(tf.int32, [None])
            actions_onehot = tf.one_hot(self.actions, action_size,dtype=tf.float32)

            Q = tf.reduce_sum(tf.multiply(self.q_out, actions_onehot), axis=1)

            td_error = tf.square(self.target_q - Q)

            mask_a = tf.zeros([self.batch_size, self.train_len//2])
            mask_b = tf.ones([self.batch_size, self.train_len//2])

            mask = tf.concat([mask_a, mask_b], 1)
            mask = tf.reshape(mask, [-1])

            self.loss = tf.reduce_mean(td_error * mask)

        self.update = tf.train.AdamOptimizer(learning_rate=learning_rate).minimize(self.loss)


class ExperienceBuffer():
    def __init__(self, buffer_size=1000):
        self.buffer = []
        self.buffer_size = buffer_size

    def add(self, experience):
        self.buffer.append(experience)
        if len(self.buffer) > self.buffer_size:
            self.buffer.pop(0)

    def sample(self, batch_size, trace_len):
        samples_ep = random.sample(self.buffer, batch_size)
        samples_traces = []
        for episode in samples_ep:
            pt = np.random.randint(0, len(episode) + 1 - trace_len)
            samples_traces.append(episode[pt:pt+trace_len])

        samples_traces = np.array(samples_traces)
        return np.reshape(samples_traces, [batch_size * trace_len, 5]) # why 5 ?

def process_frame(f, width):
    return np.reshape(f[:width,:width,:],[-1])

def discounted_reward(rs, gamma):
    total = 0
    for k in reversed(range(len(rs))):
        total = total * gamma + rs[k]

    return total

if __name__=="__main__":
    env = gym.make('MsPacman-v0')

    img_width = 160
    frame_size = img_width * img_width * 3

    batch_size = 4 # num of experience traces
    trace_len = 8
    update_step = 5

    gamma = 0.99 # discount factor for reward
    e_start = 1 # prob of random action
    e_end = 0.1
    annel_steps  = 10000 # steps from e_start to e_end
    total_episodes = 1000

    pre_train_steps = 3000 # steps of random action before training begins
    logdir = "./checkpoints"

    h_size = 512
    tau = 0.001
    action_size = env.action_space.n

    e_delta = (e_start - e_end) / annel_steps
    exp_buffer = ExperienceBuffer()

    # build graph
    graph = tf.Graph()
    with graph.as_default():
        #with tf.variable_scope("qnetwork"):
        main_qn = QNetwork(h_size, action_size, scope="main_qn", img_size=img_width)

        #with tf.variable_scope("qnetwork", reuse=True):
        target_qn = QNetwork(h_size, action_size, reuse=True, scope="target_qn", img_size=img_width)

    sv = tf.train.Supervisor(logdir=logdir, graph=graph)
    e = e_start
    total_step = 0

    with sv.managed_session() as sess:
        for ep in range(total_episodes):
            ep_buffer = []
            s = env.reset()
            s_frame = process_frame(s, img_width)

            rnn_state = (np.zeros([1, h_size]),np.zeros([1, h_size]))
            ep_rewards = []

            while True:
                env.render()
                feed_dict = {
                    main_qn.frame_in: [s_frame / 255.0],
                    main_qn.train_len: 1,
                    main_qn.state_init: rnn_state,
                    main_qn.batch_size: 1
                }
                if np.random.rand() < e or total_step<pre_train_steps:
                    state_rnn1 = sess.run(main_qn.rnn_state, feed_dict=feed_dict)
                    act = np.random.randint(0, action_size)
                else:
                    act, state_rnn1 = sess.run([main_qn.pred, main_qn.rnn_state], feed_dict=feed_dict)

                s1, reward, done, obs = env.step(act)
                s1_frame = process_frame(s1, img_width)
                ep_rewards.append(reward)

                total_step += 1
                ep_buffer.append(np.reshape(np.array([s_frame, act, reward, s1_frame, done]), [1,5]))

                if total_step > pre_train_steps:
                    if e > e_end:
                        e -= e_delta

                    if total_step % update_step == 0:
                        # update model
                        state_train = (np.zeros([batch_size, h_size]), np.zeros([batch_size, h_size]))
                        train_batch = exp_buffer.sample(batch_size, trace_len)

                        dict_main = {
                            main_qn.frame_in:np.vstack(train_batch[:, 3]/255.0),
                            main_qn.train_len:trace_len,
                            main_qn.state_init:state_train,
                            main_qn.batch_size:batch_size
                        }
                        q_main = sess.run(main_qn.pred, feed_dict=dict_main)

                        dict_target = {
                            target_qn.frame_in:np.vstack(train_batch[:, 3]/255.0),
                            target_qn.train_len:trace_len,
                            target_qn.state_init:state_train,
                            target_qn.batch_size:batch_size
                        }
                        q_target = sess.run(target_qn.q_out, feed_dict=dict_target)
                        #print(q_target)
                        #print(np.shape(q_target))

                        end_multiplier = - (train_batch[:, 4] - 1)
                        double_q = q_target[range(batch_size * trace_len),q_main]
                        target_q_val = train_batch[:, 2] + gamma * double_q * end_multiplier
                        #print(np.shape(double_q))
                        #print(np.shape(end_multiplier))
                        #print(np.shape(target_q_val))

                        update_dict = {
                            main_qn.frame_in:np.vstack(train_batch[:,0]/255.0),
                            main_qn.target_q:target_q_val,
                            main_qn.actions:train_batch[:,1],
                            main_qn.train_len:trace_len,
                            main_qn.state_init:state_train,
                            main_qn.batch_size:batch_size
                        }
                        sess.run(main_qn.update, feed_dict=update_dict)

                s = s1
                s_frame = s1_frame
                rnn_state = state_rnn1

                if done:
                    disc_r = discounted_reward(ep_rewards, gamma)
                    print("Episode {} finished with discounted reward {}".format(ep, disc_r))
                    break

            # add episode to experience buffer
            step_buffer = np.array(ep_buffer)
            step_buffer = list(zip(step_buffer))
            exp_buffer.add(step_buffer)




