#!/usr/bin/env python2

import sys
import rospy
import numpy as np
from gazebo_msgs.msg import ModelStates
from geometry_msgs.msg import Twist, Vector3
from rosgraph_msgs.msg import Clock
from MPC_ORCA import MPC_ORCA
from pyorca import Agent

robot = int(sys.argv[1])
goal = np.array([float(sys.argv[2]), float(sys.argv[3])])
RADIUS = 0.4
tau = 10
N = 10
Ts = 0.1
V_min = -1
V_max = 1

rospy.init_node('mpc_orca_controller_robot_' + str(robot))

# Waiting gazebo first message
data = rospy.wait_for_message('/gazebo/model_states', ModelStates)

n_robots = len(data.name) - 1

X = [np.zeros(2) for _ in range(n_robots)]
V = [np.zeros(2) for _ in range(n_robots)]
orientation = [0.0 for _ in range(n_robots)]
model = [i+1 for i in range(n_robots)]

# Getting robot model order on gazebo model_states
for i, value in enumerate(data.name):
    # Skipping i == 0 because it's the ground_plane state
    if i > 0:
        idx = value.split('_')
        model[int(idx[1])] = i

# Agents list
agents = []
for i in range(n_robots):
    agents.append(Agent(X[i], np.zeros(2), np.zeros(2), RADIUS))

def velocityTransform(v, theta_0):
    
    linear = np.sqrt(v[0]**2 + v[1]**2)
    angular = np.arctan2(v[1], v[0]) - theta_0 

    if np.abs(angular) > 2*np.pi/3:
        angular -= np.sign(angular) * np.pi
        linear = -linear
    if np.abs(linear) < 0.001:
        angular = 0
        linear = 0        
 
    return [linear, angular]

def update_positions(agents):
    for i in range(n_robots):
        agents[i].position = np.array(X[i])
        agents[i].velocity = np.array(V[i])
    return agents

def updateWorld(msg):
    for i in range(n_robots):
        X[i] = np.array([float(msg.pose[model[i]].position.x), float(msg.pose[model[i]].position.y)])
        V[i] = np.array([float(msg.twist[model[i]].linear.x)/2, float(msg.twist[model[i]].linear.y)/2])
        orientation[i] = np.arctan2(2 * float(msg.pose[model[i]].orientation.w) * float(msg.pose[model[i]].orientation.z), 1 - 2 * float(msg.pose[model[i]].orientation.z)**2)
        
# Subscribing on model_states instead of robot/odom, to avoid unnecessary noise
rospy.Subscriber('/gazebo/model_states', ModelStates, updateWorld)

# Velocity publisher
pub = rospy.Publisher('/robot_' + str(robot) + '/cmd_vel', Twist, queue_size=10)

# Setpoint Publishers
pub_setpoint_pos = rospy.Publisher('/robot_' + str(robot) + '/setpoint_pos', Vector3, queue_size=10)
pub_setpoint_vel = rospy.Publisher('/robot_' + str(robot) + '/setpoint_vel', Vector3, queue_size=10)

setpoint_pos = Vector3()
setpoint_vel = Vector3()

# Initializing Controllers
colliders = agents[:robot] + agents[robot + 1:]
controller = MPC_ORCA(agents[robot].position, V_min, V_max, N, Ts, colliders, tau, RADIUS)

# Global path planning
initial = np.copy(X[robot])
t_max = 10.0
growth = 0.5
logistic = lambda t: 1/(1 + np.exp(- growth * (t - t_max)))
d_logistic = lambda t: growth * logistic(t) * (1 - logistic(t))
P_des = lambda t: goal * logistic(t) + initial * (1 - logistic(t))
V_des = lambda t: goal * d_logistic(t) - initial * d_logistic(t)

t = 0

while not rospy.is_shutdown():

    agents = update_positions(agents)
    
    # Updating controller agents
    controller.agent = agents[robot]
    controller.colliders = agents[:robot] + agents[robot + 1:]
    
    # Updating setpoint trajectory
    setpoint = np.ravel([np.append(P_des(t + k * Ts), V_des(t + k * Ts)) for k in range(0, N + 1)])

    # Computing optimal input values
    [agents[robot].velocity, agents[robot].acceleration] = controller.getNewVelocity(setpoint)

    [setpoint_pos.x, setpoint_pos.y] = P_des(t)

    [setpoint_vel.x, setpoint_vel.y] = V_des(t)

    vel = Twist()
    [vel.linear.x, vel.angular.z] = velocityTransform(agents[robot].velocity, orientation[robot])
    
    pub.publish(vel)
    
    pub_setpoint_pos.publish(setpoint_pos)
    pub_setpoint_vel.publish(setpoint_vel)
    rospy.sleep(Ts)

    t += Ts
