package main

deny[msg] {
  not input.services.app
  msg := "compose file must define app service"
}

deny[msg] {
  input.services.app.restart != "unless-stopped"
  msg := "app service restart policy must be unless-stopped"
}

deny[msg] {
  not contains(input.services.app.labels[_], "traefik.enable=true")
  msg := "app service must enable traefik"
}
