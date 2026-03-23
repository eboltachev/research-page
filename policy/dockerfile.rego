package main

deny[msg] {
  input[0].Cmd != "from"
  msg := "Dockerfile must start with FROM"
}

deny[msg] {
  some i
  input[i].Cmd == "from"
  endswith(lower(input[i].Value[0]), ":latest")
  msg := "Avoid latest tag in Dockerfile base image"
}

deny[msg] {
  not has_user
  msg := "Dockerfile must define non-root USER"
}

has_user {
  some i
  input[i].Cmd == "user"
}
