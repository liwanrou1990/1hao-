/** Hardhat is used only as a local EVM / mainnet-fork node (Anvil equivalent). No compilation via hardhat. */
module.exports = {
  solidity: "0.8.28",
  networks: {
    hardhat: { chainId: 8453, allowUnlimitedContractSize: true, mining: { auto: true } }
  },
  paths: { sources: "./contracts_unused", artifacts: "./artifacts/hardhat", cache: "./cache" }
};
