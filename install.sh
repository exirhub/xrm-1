cd /root
wget https://github.com/exirhub/exirvpn-balancer-config/raw/refs/heads/main/x-ui.db
echo "n" | bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh)
sudo systemctl stop x-ui
sudo chmod +x /root/x-ui.db
sudo cp /root/x-ui.db /etc/x-ui/x-ui.db
sudo systemctl start x-ui
ufw disable
sudo fallocate -l 1G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
sudo echo '/swapfile none swap sw 0 0' >> /etc/fstab

echo "Applying TCP buffer optimizations..."
sudo sysctl -w net.core.rmem_max=67108864
sudo sysctl -w net.core.wmem_max=67108864
sudo sysctl -w net.core.netdev_max_backlog=100000
echo "net.ipv4.tcp_keepalive_time = 60" >> /etc/sysctl.conf
echo "net.ipv4.tcp_keepalive_intvl = 10" >> /etc/sysctl.conf
echo "net.ipv4.tcp_keepalive_probes = 6" >> /etc/sysctl.conf
sysctl -p
# Persist changes in sysctl.conf
echo "Saving settings to /etc/sysctl.conf..."
sudo cat <<EOF >> /etc/sysctl.conf
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.core.netdev_max_backlog = 100000
EOF
# Apply settings
sysctl -p

echo "TCP buffer optimizations applied successfully!"
sudo cp /root/x-ui.db /etc/x-ui/x-ui.db
sudo systemctl restart x-ui
wget https://raw.githubusercontent.com/exirhub/exirvpn-balancer-config/refs/heads/main/receiver.sh
chmod +x receiver.sh
./receiver.sh

