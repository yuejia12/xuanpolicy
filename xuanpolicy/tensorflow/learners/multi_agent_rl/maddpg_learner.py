"""
Multi-Agent Deep Deterministic Policy Gradient
Paper link:
https://proceedings.neurips.cc/paper/2017/file/68a9750337a418a86fe06c1991a1d64c-Paper.pdf
Implementation: TensorFlow 2.X
Trick: Parameter sharing for all agents, with agents' one-hot IDs as actor-critic's inputs.
"""
from xuanpolicy.tensorflow.learners import *


class MADDPG_Learner(LearnerMAS):
    def __init__(self,
                 config: Namespace,
                 policy: tk.Model,
                 optimizer: Sequence[tk.optimizers.Optimizer],
                 summary_writer: Optional[SummaryWriter] = None,
                 device: str = "cpu:0",
                 modeldir: str = "./",
                 gamma: float = 0.99,
                 sync_frequency: int = 100
                 ):
        self.gamma = gamma
        self.tau = config.tau
        self.sync_frequency = sync_frequency
        super(MADDPG_Learner, self).__init__(config, policy, optimizer, summary_writer, device, modeldir)
        self.optimizer = {
            'actor': optimizer[0],
            'critic': optimizer[1]
        }

    def save_model(self):
        model_path = self.modeldir + "model-%s-%s" % (time.asctime(), str(self.iterations))
        self.policy.actor_net.save(model_path)

    def load_model(self, path):
        model_names = os.listdir(path)
        try:
            model_names.sort()
            model_path = path + model_names[-1]
            self.policy.actor_net = tk.models.load_model(model_path, compile=False)
        except:
            raise "Failed to load model! Please train and save the model first."

    def update(self, sample):
        self.iterations += 1
        with tf.device(self.device):
            obs = tf.convert_to_tensor(sample['obs'])
            actions = tf.convert_to_tensor(sample['actions'])
            obs_next = tf.convert_to_tensor(sample['obs_next'])
            rewards = tf.convert_to_tensor(sample['rewards'])
            terminals = tf.reshape(tf.convert_to_tensor(sample['terminals'], dtype=tf.float32), [-1, self.n_agents, 1])
            agent_mask = tf.reshape(tf.convert_to_tensor(sample['agent_mask'], dtype=tf.float32),
                                    [-1, self.n_agents, 1])
            IDs = tf.tile(tf.expand_dims(tf.eye(self.n_agents), axis=0), multiples=(self.args.batch_size, 1, 1))

            # train actor
            with tf.GradientTape() as tape:
                inputs = {"obs": obs, "ids": IDs}
                _, actions_eval = self.policy(inputs)
                loss_a = -tf.reduce_sum(self.policy.critic(obs, actions_eval, IDs) * agent_mask) / tf.reduce_sum(agent_mask)
                gradients = tape.gradient(loss_a, self.policy.parameters_actor)
                self.optimizer['actor'].apply_gradients([
                    (tf.clip_by_norm(grad, self.args.clip_grad), var)
                    for (grad, var) in zip(gradients, self.policy.parameters_actor)
                    if grad is not None
                ])

            # train critic
            with tf.GradientTape() as tape:
                inputs_next = {"obs": obs_next, "ids": IDs}
                actions_next = self.policy.target_actor(inputs_next)
                q_eval = self.policy.critic(obs, actions, IDs)
                q_next = self.policy.target_critic(obs_next, actions_next, IDs)
                if self.args.consider_terminal_states:
                    q_target = rewards + (1 - terminals) * self.args.gamma * q_next
                else:
                    q_target = rewards + self.args.gamma * q_next
                y_pred = tf.reshape(q_eval * agent_mask, [-1])
                y_true = tf.reshape(q_target * agent_mask, [-1])
                loss_c = tk.losses.mean_squared_error(y_true, y_pred)
                gradients = tape.gradient(loss_c, self.policy.parameters_critic)
                self.optimizer['critic'].apply_gradients([
                    (tf.clip_by_norm(grad, self.args.clip_grad), var)
                    for (grad, var) in zip(gradients, self.policy.parameters_critic)
                    if grad is not None
                ])

            self.policy.soft_update(self.tau)

            lr_a = self.optimizer['actor']._decayed_lr(tf.float32)
            lr_c = self.optimizer['critic']._decayed_lr(tf.float32)
            self.writer.add_scalar("learning_rate_actor", lr_a.numpy(), self.iterations)
            self.writer.add_scalar("learning_rate_critic", lr_c.numpy(), self.iterations)
            self.writer.add_scalar("loss_actor", loss_a.numpy(), self.iterations)
            self.writer.add_scalar("loss_critic", loss_c.numpy(), self.iterations)
            self.writer.add_scalar("predictQ", tf.math.reduce_mean(q_eval).numpy(), self.iterations)
