#cloud-config
runcmd:
  - 'cd /root && wget -q https://raw.githubusercontent.com/exirhub/exirvpn-balancer-config/main/install.sh -O install.sh'
  - 'chmod +x install.sh'
  - 'bash install.sh'

  cd /root && wget -q https://raw.githubusercontent.com/exirhub/exirvpn-balancer-config/main/install.sh -O install.sh
  chmod +x install.sh
  bash install.sh
