zsh::init() {
    autoload -Uz add-zsh-hook
    autoload -U colors && colors
    autoload -Uz compaudit
    compaudit
    autoload -Uz compinit
    compinit -C -d $HOME/.zcompdump
}

# make it so I can cd into work directories from anywhere
zsh::cdpath::init() {
    cdpath = ()
    cdpath += ("$HOME/arkestro")
    cdpath += ("$HOME")
}

zsh::history::init() {
    setopt BANG_HIST              # Treat the '!' character specially during expansion.
    setopt EXTENDED_HISTORY       # Write the history file in the ':start:elapsed;command' format.
    setopt SHARE_HISTORY          # Share history between all sessions.
    setopt HIST_EXPIRE_DUPS_FIRST # Expire a duplicate event first when trimming history.
    setopt HIST_IGNORE_DUPS       # Do not record an event that was just recorded again.
    setopt HIST_IGNORE_ALL_DUPS   # Delete an old recorded event if a new event is a duplicate.
    setopt HIST_FIND_NO_DUPS      # Do not display a previously found event.
    setopt HIST_IGNORE_SPACE      # Do not record an event starting with a space.
    setopt HIST_SAVE_NO_DUPS      # Do not write a duplicate event to the history file.
    setopt HIST_VERIFY            # Do not execute immediately upon history expansion.
    setopt HIST_BEEP              # Beep when accessing non-existent history.

    export HISTFILE="$ZDOTDIR/.zsh_history"
    export HISTSIZE=100000
    export SAVEHIST=100000

}

homebrew::bootstrap() {
      /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
}

zsh::colors::init() {
    . "$HOME/zsh-users/zsh-syntax-highlighting/zsh-syntax-highlighting.zsh"
}

shell::path::init() {
    # export PATH=""
    export PATH="$PATH:/opt/X11/bin" 
    export PATH="$PATH:/opt/homebrew/bin"
    export PATH="$PATH:/opt/homebrew/sbin"
    export PATH="$PATH:/usr/local/bin"
    export PATH="$PATH:/usr/local/sbin"
    export PATH="$PATH:/usr/bin"
    export PATH="$PATH:/usr/sbin"
    export PATH="$PATH:/bin"
    export PATH="$PATH:/sbin"
}

shell::env::init() {
    export AWS_PAGER=””
    export GH_PAGER=””
    export GIT_PAGER=””
    export PAGER=””
    export MANPAGER=”sh -c 'col -bx | bat -l man -p'”

    export EDITOR="nano" # zsh will set key bindings based on this
    # or use bindkey -v or bindkey -e 

    export LESS="-R"
    export RUBY_SHELL="ripe"
    # export VIRTUAL_ENV_DISABLE_PROMPT=true

}

homebrew::init() {
    local homebrew_prefix=$(brew --config | awk -F: '/PREFIX/ { print $2 }' | sed -e 's, ,,g')
    export HOMEBREW_NO_ANALYTICS=1
    export HOMEBREW_EDITOR=nano
    export HOMEBREW_PREFIX="$homebrew_prefix"
}

# not to be run every time
install::fun::commands() {
    brew install –cask 1password
    brew install –cask cloudflare-warp
    brew install –cask iterm2

    brew install 1password-cli

    brew install ack
    brew install ag
    brew install bat
    brew install fd
    brew install rg
    brew install jc
    brew install jp
    brew install jq
    brew install yq
    brew install pdfgrep
    brew install xsv

    brew install git
    brew install git-delta
    brew install git-extras
    brew install git-lfs
    brew install git-quick-stats
    brew install git-secret
    brew install tig
    brew install gh
    brew install act

}


zsh::init
shell::path::init
zsh::cdpath::init
zsh::history::init
zsh::colors::init
shell::env::init
homebrew::init
