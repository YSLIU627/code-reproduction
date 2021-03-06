import torch
import torch.nn as nn
from torch.distributions import MultivariateNormal
import gym
import numpy as np
torch.set_default_tensor_type(torch.FloatTensor)
device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

############## Hyperparameters ##############
env_name = "BipedalWalker-v3"
render = False
solved_reward = 300         # stop training if average_reward > solved_reward
log_interval = 20           # print average reward in the interval
max_episodes = 10000        # max training episodes
max_timesteps = 1500        # max timesteps in one episode
    
update_timestep = 4000      # update policy every n timesteps
action_std = 0.5            # constant std for action distribution (Multivariate Normal)
K_epochs = 80               # update policy for K epochs
eps_clip = 0.2              # clip parameter for PPO
gamma = 0.99                # discount factor
    
lr = 0.0003                 # parameters for Adam optimizer
betas = (0.9, 0.999)
    
random_seed = None

class ActorCritic(nn.Module):
    def __init__(self, state_dim, action_dim, action_std):
        super(ActorCritic,self).__init__()
        # here activator tanh is adopted because action mean ranges -1 to 1
        # actor : output the mean of the policy
        self.actor =  nn.Sequential(
                nn.Linear(state_dim, 64),
                nn.Tanh(),
                nn.Linear(64, 32),
                nn.Tanh(),
                nn.Linear(32, action_dim),
                nn.Tanh()
                )
        # critic : estimate Advantage function 
        self.critic = nn.Sequential(
                nn.Linear(state_dim, 64),
                nn.Tanh(),
                nn.Linear(64, 32),
                nn.Tanh(),
                nn.Linear(32, 1)
                )
        # define the variance of the policy (here we adopt Gaussian policy)
        self.action_var = torch.full((action_dim,), action_std*action_std).to(device)
    def forward(self):
        raise ImportError
    def act(self,state,memory) :
        action_mean = self.actor(state)
        cov_mat = torch.diag(self.action_var).to(device)
        distribution = MultivariateNormal(action_mean, cov_mat)
        action = distribution.sample()
        action_logprob = distribution.log_prob(action)

        #log in the memory

        memory.states.append(state)
        memory.actions.append(action)
        memory.logprobs.append(action_logprob)
        # detach action from grad graph
        return action.detach() 

    def evaluate(self, state, action):
        # used in the update
        action_mean = self.actor(state)
        action_var= self.action_var.expand_as(action_mean)
        cov_mat = torch.diag_embed(action_var).to(device)

        distribution = MultivariateNormal(action_mean, cov_mat)
        cov_mat = torch.diag_embed(action_var).to(device)
        action_logprob = distribution.log_prob(action)
        dist_entropy = distribution.entropy()
        state_value = self.critic(state)
        return action_logprob, torch.squeeze(state_value), dist_entropy

class PPO() :
    def __init__(self, state_dim, action_dim, action_std, lr, betas, gamma, K_epochs, eps_clip):
        self.lr = lr
        self.betas = betas
        self.gamma = gamma
        self.eps_clip = eps_clip
        self.K_epochs = K_epochs
        
        self.policy = ActorCritic(state_dim, action_dim, action_std).to(device)
        self.optimizer = torch.optim.Adam(self.policy.parameters(), lr=lr, betas=betas)
        
        self.policy_old = ActorCritic(state_dim, action_dim, action_std).to(device)
        # initial 
        self.policy_old.load_state_dict(self.policy.state_dict())
        
        self.MseLoss = nn.MSELoss()
    def action_selection(self, state, memory):
        # select action according to the old policy
        state = torch.FloatTensor(state.reshape(1, -1)).to(device)
        return self.policy_old.act(state, memory).cpu().data.numpy().flatten()
    
    def update (self, memory):
        # MC estimate of Return
        Returns = []
        disc_reward = 0
        for reward , is_terminal in zip(reversed(memory.rewards),reversed(memory.is_terminals)) :
            if is_terminal:
                disc_reward = 0
            disc_reward = reward + self.gamma* disc_reward
            # newest in the first
            Returns.insert(0,disc_reward)       

        # Normalizing the Returns:here Returns mean return in MC
        Returns = torch.tensor(Returns).to(device).float()
        Returns = (Returns - Returns.mean()) / (Returns.std() + 1e-5)
        # convert list to tensor, old ones need not to be in the grad graph
        old_states = torch.squeeze(torch.stack(memory.states).to(device), 1).detach()
        old_actions = torch.squeeze(torch.stack(memory.actions).to(device), 1).detach()
        old_logprobs = torch.squeeze(torch.stack(memory.logprobs), 1).to(device).detach()

        # optimize for K epochs:
        for j in range(self.K_epochs):
            # evalute the old 
            log_probs, state_values, dist_entropy = self.policy.evaluate(old_states,old_actions)
        # finding the ratio (pi_theta / pi_theta__old):
            ratios = torch.exp(log_probs - old_logprobs.detach()).float()

        # find the surrogate loss
        
        advantages = Returns- state_values.detach()
        L_CPI = ratios* advantages
        L_CLIP = torch.min(L_CPI,torch.clamp(ratios,1-self.eps_clip,1+self.eps_clip)*advantages)
        loss = -L_CLIP + 0.5 * self.MseLoss(state_values, Returns)- 0.01 *dist_entropy
        
        # Take gradient step
        self.optimizer.zero_grad()
        loss.mean().backward()
        self.optimizer.step()
        # Copy new weights into old policy:
        self.policy_old.load_state_dict(self.policy.state_dict())
class Memory:
    def __init__(self):
        self.actions = []
        self.states = []
        self.logprobs = []
        self.rewards = []
        self.is_terminals = []
    
    def clear_memory(self):
        del self.actions[:]
        del self.states[:]
        del self.logprobs[:]
        del self.rewards[:]
        del self.is_terminals[:]
def main():
    
    # creating environment
    env = gym.make(env_name)
    state_dim = env.observation_space.shape[0]
    action_dim = env.action_space.shape[0]
    
    if random_seed:
        print("Random Seed: {}".format(random_seed))
        torch.manual_seed(random_seed)
        env.seed(random_seed)
        np.random.seed(random_seed)
    
    memory = Memory()
    ppo = PPO(state_dim, action_dim, action_std, lr, betas, gamma, K_epochs, eps_clip)
    print(lr,betas)
    
    # logging variables
    running_reward = 0
    average_length = 0
    time_step = 0
    
    # training loop
    for i_episode in range(1, max_episodes+1):
        state = env.reset()
        for t in range(max_timesteps):
            time_step +=1
            # Running policy_old:
            action = ppo.action_selection(state, memory)
            state, reward, done, _ = env.step(action)
            
            # Saving reward and is_terminals:
            memory.rewards.append(reward)
            memory.is_terminals.append(done)
            
            # update if its time
            if time_step % update_timestep == 0:
                ppo.update(memory)
                memory.clear_memory()
                time_step = 0
            running_reward += reward
            if render:
                env.render()
            if done:
                break
        
        average_length += t
        
        # save every 500 episodes
        if i_episode % 500 == 0:
            torch.save(ppo.policy.state_dict(), './PPO_continuous_{}.pth'.format(env_name))
            
        # logging
        if i_episode % log_interval == 0:
            average_length = int(average_length/log_interval)
            running_reward = int((running_reward/log_interval))
            
            print('Episode {} \t average length: {} \t average reward: {}'.format(i_episode, average_length, running_reward))
            running_reward = 0
            average_length = 0
            
if __name__ == '__main__':
    main()
